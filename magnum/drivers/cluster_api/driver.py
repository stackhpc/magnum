# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import enum
import re

import certifi
import keystoneauth1
from kubernetes import client
from kubernetes import config
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import encodeutils
import yaml

from magnum.api import utils as api_utils
from magnum.common import clients
from magnum.common import utils
from magnum.common.x509 import operations as x509
from magnum.conductor.handlers.common import cert_manager
from magnum.drivers.cluster_api import helm
from magnum.drivers.cluster_api import kubernetes
from magnum.drivers.common import driver
from magnum.objects import fields


LOG = logging.getLogger(__name__)


# TODO(mkjpryor) these constants should come from config
MAGNUM_NAMESPACE_TPL = "magnum-{project_id}"
MAGNUM_HELM_CHART_REPO = "https://stackhpc.github.io/capi-helm-charts"
MAGNUM_HELM_CHART_NAME = "openstack-cluster"
MAGNUM_HELM_CHART_VERSION = "0.1.1-dev.0.main.39"


class ClusterStatus(enum.Enum):
    READY = 1
    PENDING = 2
    ERROR = 3
    NOT_FOUND = 4


class Driver(driver.Driver):
    @property
    def provides(self):
        return [
            {
                "server_type": "vm",
                # NOTE(johngarbutt) we could support any cluster api
                # supported image, but lets start with ubuntu for now.
                # TODO(johngarbutt) os list should probably come from config?
                "os": "ubuntu",
                "coe": "kubernetes",
            }
        ]

    def __init__(self):
        self._k8s_client = None
        self._helm_client = None

    @property
    def k8s_client(self):
        if self._k8s_client:
            return self._k8s_client
        client_config = type.__call__(client.Configuration)
        config.load_config(client_configuration=client_config)
        self._k8s_client = client.ApiClient(configuration=client_config)
        return self._k8s_client

    @property
    def helm_client(self):
        if self._helm_client:
            return self._helm_client
        self._helm_client = helm.Client()
        return self._helm_client

    def _label(self, cluster, key, default):
        return cluster.labels.get(
            key, cluster.cluster_template.labels.get(key, default)
        )

    def _k8s_resource_labels(self, cluster):
        return {
            "magnum.openstack.org/project-id": cluster.project_id,
            "magnum.openstack.org/user-id": cluster.user_id,
            "magnum.openstack.org/cluster-uuid": cluster.uuid,
        }

    def _namespace(self, cluster):
        # We create clusters in a project-specific namespace
        # To generate the namespace, first sanitize the project id
        project_id = re.sub("[^a-z0-9]", "", cluster.project_id.lower())
        return MAGNUM_NAMESPACE_TPL.format(project_id=project_id)

    def _sanitised_name(self, name, suffix=None):
        return re.sub(
            "[^a-z0-9]+",
            "-",
            (f"{name}-{suffix}" if suffix else name).lower(),
        )

    def _update_helm_release(self, context, cluster):
        cluster_template = cluster_template or cluster.cluster_template
        image_uuid = self._get_image_uuid(context, cluster_template.image_id)
        values = {
            "kubernetesVersion": cluster_template.labels["kube_tag"].lstrip(
                "v"
            ),
            "machineImageId": image_uuid,
            "cloudCredentialsSecretName": self._sanitised_name(
                cluster.name, "cloud-credentials"
            ),
            "clusterNetworking": {
                "internalNetwork": {
                    "nodeCidr": self._label(
                        cluster, "fixed_subnet_cidr", "10.0.0.0/24"
                    )
                }
            },
            "apiServer": {"enableLoadBalancer": cluster.master_lb_enabled},
            "controlPlane": {
                "machineFlavor": cluster.master_flavor_id,
                "machineCount": cluster.master_count,
            },
            "nodeGroups": [
                {
                    "name": self._sanitised_name(ng.name),
                    "machineFlavor": ng.flavor_id,
                    "machineCount": ng.node_count,
                }
                for ng in cluster.nodegroups
                if ng.role != "master"
            ],
        }
        if cluster_template.dns_nameserver:
            dns_nameservers = cluster_template.dns_nameserver.split(",")
            values["clusterNetworking"]["dnsNameservers"] = dns_nameservers
        if cluster.keypair:
            values["machineSSHKeyName"] = cluster.keypair
        self.helm_client.install_or_upgrade(
            self._sanitised_name(cluster.name),
            MAGNUM_HELM_CHART_NAME,
            values,
            repo=MAGNUM_HELM_CHART_REPO,
            version=MAGNUM_HELM_CHART_VERSION,
            namespace=self._namespace(cluster),
        )

    def _get_image_uuid(self, context, image_identifier):
        image = api_utils.get_openstack_resource(
            osc.glance().images, image_identifier, 'images')
        return image.id

    def _update_status_updating(self, cluster, capi_cluster):
        # As soon as we know the API address, we should set it
        # This means users can access the API even if the create is
        # not complete, which could be useful for debugging failures,
        # e.g. with addons
        api_endpoint = capi_cluster["spec"].get("controlPlaneEndpoint")
        if api_endpoint:
            api_address = (
                f"https://{api_endpoint['host']}:{api_endpoint['port']}"
            )
            if cluster.api_address != api_address:
                cluster.api_address = api_address
                cluster.save()

        # If the cluster is not yet ready then the create/update
        # is still in progress
        true_conditions = {
            cond["type"]
            for cond in capi_cluster.get("status", {}).get("conditions", [])
            if cond["status"] == "True"
        }
        for cond in ("InfrastructureReady", "ControlPlaneReady", "Ready"):
            if cond not in true_conditions:
                return

        # TODO(johngarbutt): we need to check for timeout!
        if cluster.status == fields.ClusterStatus.CREATE_IN_PROGRESS:
            cluster.status = fields.ClusterStatus.CREATE_COMPLETE
        else:
            cluster.status = fields.ClusterStatus.UPDATE_COMPLETE
        cluster.save()
        return True

    def _update_status_deleting(self, context, cluster):
        # Once the Cluster API cluster is gone, we need to clean up
        # the secrets we created
        kubernetes.Secret(self.k8s_client).delete_all_by_label(
            "magnum.openstack.org/cluster-uuid",
            cluster.uuid,
            self._namespace(cluster),
        )

        # We also need to clean up the appcred that we made
        osc = clients.OpenStackClients(context)
        try:
            appcred = osc.keystone().client.application_credentials.find(
                name=f"magnum-{cluster.uuid}", user=cluster.user_id
            )
        except keystoneauth1.exceptions.http.NotFound:
            pass
        else:
            appcred.delete()

        cluster.status = fields.ClusterStatus.DELETE_COMPLETE
        cluster.save()

    def update_cluster_status(self, context, cluster):
        #  NOTE(mkjpryor)
        # Cluster API clusters don't really into an error state,
        # they just keep trying
        # Hence we only currently handle transitioning from IN_PROGRESS
        # states to COMPLETE
        # It is possible we could be more sophisticated in checking for
        # specific reasons
        # and transitioning to {CREATE,UPDATE}_FAILED in the future

        capi_cluster = kubernetes.Cluster(self.k8s_client).fetch(
            self._sanitised_name(cluster.name),
            namespace=self._namespace(cluster),
        )

        if cluster.status in {
            fields.ClusterStatus.CREATE_IN_PROGRESS,
            fields.ClusterStatus.UPDATE_IN_PROGRESS,
        }:
            LOG.debug("Checking on an update for %s", cluster.uuid)
            # If the cluster does not exist yet,
            # create is still in progress
            if not capi_cluster:
                return
            self._update_status_updating(cluster, capi_cluster)

        elif cluster.status == fields.ClusterStatus.DELETE_IN_PROGRESS:
            LOG.debug("Checking on a delete for %s", cluster.uuid)
            # If the Cluster API cluster still exists,
            # the delete is still in progress
            if capi_cluster:
                return
            self._update_status_deleting(context, cluster)

    def create_cluster(self, context, cluster, cluster_create_timeout):
        LOG.info("Starting to create cluster %s", cluster.uuid)

        # Ensure that the Magnum namespace exists
        namespace = self._namespace(cluster)
        kubernetes.Namespace(self.k8s_client).apply(namespace)

        # Create an application credential for the cluster
        # and store it in a secret
        osc = clients.OpenStackClients(context)
        appcred = osc.keystone().client.application_credentials.create(
            user=cluster.user_id,
            name=f"magnum-{cluster.uuid}",
            description=f"Magnum cluster ({cluster.uuid})",
        )
        ca_certificate = utils.get_openstack_ca()
        if not ca_certificate:
            with open(certifi.where(), "r") as ca_file:
                ca_certificate = ca_file.read()
        creds = {
            "identity_api_version": 3,
            "region_name": osc.cinder_region_name(),
            "interface": cfg.CONF.nova_client.endpoint_type.replace(
                "URL", ""
            ),
            "verify": cfg.CONF.drivers.verify_ca,
            "auth": {
                "auth_url": osc.url_for(
                    service_type="identity", interface="public"
                ),
                "application_credential_id": appcred.id,
                "application_credential_secret": appcred.secret,
            },
        }
        kubernetes.Secret(self.k8s_client).apply(
            self._sanitised_name(cluster.name, "cloud-credentials"),
            {
                "metadata": {"labels": self._k8s_resource_labels(cluster)},
                "stringData": {
                    "cacert": ca_certificate,
                    "clouds.yaml": yaml.safe_dump(
                        {"clouds": {"openstack": creds}}
                    ),
                },
            },
            self._namespace(cluster),
        )

        # Create secrets for the certificates stored in Barbican
        # This is required for "openstack coe cluster config" to work,
        # as it doesn't communicate with the driver - it
        # relies on the correct certificates being trusted
        certificates = {
            "ca": cert_manager.get_cluster_ca_certificate(cluster, context),
            "etcd": cert_manager.get_cluster_ca_certificate(
                cluster, context, "etcd"
            ),
            "proxy": cert_manager.get_cluster_ca_certificate(
                cluster, context, "front_proxy"
            ),
            "sa": cert_manager.get_cluster_magnum_cert(cluster, context),
        }
        for name, ca_cert in certificates.items():
            kubernetes.Secret(self.k8s_client).apply(
                self._sanitised_name(cluster.name, name),
                {
                    "metadata": {
                        "labels": self._k8s_resource_labels(cluster)
                    },
                    "type": "cluster.x-k8s.io/secret",
                    "stringData": {
                        "tls.crt": encodeutils.safe_decode(
                            ca_cert.get_certificate()
                        ),
                        "tls.key": encodeutils.safe_decode(
                            x509.decrypt_key(
                                ca_cert.get_private_key(),
                                ca_cert.get_private_key_passphrase(),
                            )
                        ),
                    },
                },
                self._namespace(cluster),
            )

        # Install the Helm release for the cluster
        self._update_helm_release(context, cluster)

    def update_cluster(
        self, context, cluster, scale_manager=None, rollback=False
    ):
        LOG.info("Starting to update cluster %s", cluster.uuid)
        raise Exception("not implemented yet!")

    def delete_cluster(self, context, cluster):
        LOG.info("Starting to delete cluster %s", cluster.uuid)

        # Begin the deletion of the cluster by uninstalling the Helm release
        self.helm_client.uninstall_release(
            self._sanitised_name(cluster.name),
            namespace=self._namespace(cluster),
        )

    def resize_cluster(
        self,
        context,
        cluster,
        resize_manager,
        node_count,
        nodes_to_remove,
        nodegroup=None,
    ):
        raise Exception("don't support removing nodes this way yet")

    def upgrade_cluster(
        self,
        context,
        cluster,
        cluster_template,
        max_batch_size,
        nodegroup,
        scale_manager=None,
        rollback=False,
    ):
        raise NotImplementedError("don't support upgrade yet")

    def create_nodegroup(self, context, cluster, nodegroup):
        raise Exception("we don't support node groups yet")

    def update_nodegroup(self, context, cluster, nodegroup):
        raise Exception("we don't support node groups yet")

    def delete_nodegroup(self, context, cluster, nodegroup):
        raise Exception("we don't support node groups yet")

    def create_federation(self, context, federation):
        return NotImplementedError("Will not implement 'create_federation'")

    def update_federation(self, context, federation):
        return NotImplementedError("Will no implement 'update_federation'")

    def delete_federation(self, context, federation):
        return NotImplementedError("Will not implement 'delete_federation'")
