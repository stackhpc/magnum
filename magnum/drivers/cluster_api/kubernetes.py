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

import copy

# TODO(mkjpryor): use ./magnum/conductor/k8s_api.py instead
from kubernetes import client


class Resource:
    def __init__(
        self,
        api_client,
        api_version=None,
        kind=None,
        plural_name=None,
        namespaced=None,
    ):
        self.api_client = api_client
        if api_version:
            self.api_version = api_version
        if kind:
            self.kind = kind
        else:
            self.kind = getattr(self, "kind", type(self).__name__)
        if plural_name:
            self.plural_name = plural_name
        else:
            self.plural_name = getattr(
                self, "plural_name", self.kind.lower() + "s"
            )
        if namespaced is not None:
            self.namespaced = namespaced
        else:
            self.namespaced = getattr(self, "namespaced", True)

    def prepare_path(self, name=None, namespace=None):
        # Begin with either /api or /apis depending whether the api version
        # is the core API
        prefix = "/apis" if "/" in self.api_version else "/api"
        # Include the namespace unless the resource is namespaced
        path_namespace = f"/namespaces/{namespace}" if namespace else ""
        # Include the resource name if given
        path_name = f"/{name}" if name else ""
        return (
            f"{prefix}/{self.api_version}{path_namespace}/"
            f"{self.plural_name}{path_name}"
        )

    def fetch(self, name, namespace=None):
        """Fetches specified object from the target Kubernetes.

        If the object is not found, None is returned.
        """
        assert self.namespaced == bool(namespace)
        try:
            return self.api_client.call_api(
                self.prepare_path(name, namespace),
                "GET",
                response_type=object,
                _return_http_data_only=True,
            )
        except client.ApiException as exc:
            if exc.status == 404:
                return None
            else:
                raise

    def apply(self, name, data=None, namespace=None):
        """Applies the given object to the target Kubernetes cluster"""
        assert self.namespaced == bool(namespace)

        body_data = copy.deepcopy(data) if data else {}
        body_data["apiVersion"] = self.api_version
        body_data["kind"] = self.kind
        body_data.setdefault("metadata", {})["name"] = name
        if namespace:
            body_data["metadata"]["namespace"] = namespace
        return self.api_client.call_api(
            self.prepare_path(name, namespace),
            "PATCH",
            body=body_data,
            query_params=[("fieldManager", "magnum"), ("force", "true")],
            header_params={"Content-Type": "application/apply-patch+yaml"},
            response_type=object,
            _return_http_data_only=True,
        )

    def delete(self, name, namespace=None):
        """Deletes specified object from the target Kubernetes."""
        assert self.namespaced == bool(namespace)
        self.api_client.call_api(self.prepare_path(name, namespace), "DELETE")

    def delete_all_by_label(self, label, value, namespace=None):
        """Deletes all objects with the specified label from cluster."""
        assert self.namespaced == bool(namespace)
        self.api_client.call_api(
            self.prepare_path(namespace=namespace),
            "DELETE",
            query_params=[("labelSelector", label)],
        )


class Namespace(Resource):
    api_version = "v1"
    namespaced = False


class Secret(Resource):
    api_version = "v1"


class Cluster(Resource):
    api_version = "cluster.x-k8s.io/v1beta1"
