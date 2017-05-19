# Copyright 2016 Rackspace Inc. All rights reserved.
#
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

from neutronclient.common import exceptions as n_exception
from neutronclient.neutron import v2_0 as neutronV20

import os

from magnum.common import exception
from magnum.drivers.heat import swarm_fedora_template_def as sftd
from oslo_config import cfg

CONF = cfg.CONF


class FedoraSwarmIronicTemplateDefinition(sftd.SwarmFedoraTemplateDefinition):
    """Swarm template for a Fedora Baremetal."""

    def __init__(self):
        super(FedoraSwarmIronicTemplateDefinition, self).__init__()
        self.add_parameter('fixed_subnet',
                           cluster_template_attr='fixed_subnet',
                           param_type=str,
                           required=True)
        self.add_parameter('swarm_mode',
                           cluster_template_attr='swarm_mode',
                           param_type=bool)

    def get_fixed_network_id(self, osc, cluster_template):
        try:
            subnet = neutronV20.find_resource_by_name_or_id(
                osc.neutron(),
                'subnet',
                cluster_template.fixed_subnet
            )
        except n_exception.NeutronException as e:
            # NOTE(yuanying): NeutronCLIError doesn't have status_code
            # if subnet name is duplicated, NeutronClientNoUniqueMatch
            # (which is kind of NeutronCLIError) will be raised.
            if getattr(e, 'status_code', 400) < 500:
                raise exception.InvalidSubnet(message=("%s" % e))
            else:
                raise e

        if subnet['ip_version'] != 4:
            raise exception.InvalidSubnet(
                message="Subnet IP version should be 4"
            )

        return subnet['network_id']

    def get_params(self, context, cluster_template, cluster, **kwargs):
        ep = kwargs.pop('extra_params', {})

        osc = self.get_osc(context)
        ep['fixed_network'] = self.get_fixed_network_id(osc, cluster_template)
        return super(FedoraSwarmIronicTemplateDefinition,
                     self).get_params(context, cluster_template, cluster,
                                      extra_params=ep,
                                      **kwargs)

    @property
    def driver_module_path(self):
        return __name__[:__name__.rindex('.')]

    @property
    def template_path(self):
        return os.path.join(os.path.dirname(os.path.realpath(__file__)),
                            'templates/swarmcluster.yaml')
