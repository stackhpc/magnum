# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
from oslo_policy import policy

from magnum.common.policies import base

CERTIFICATE = 'certificate:%s'
RULE_ADMIN_OR_USER_OR_CLUSTER_USER = base.RULE_ADMIN_OR_USER + " or " + \
    base.RULE_CLUSTER_USER

rules = [
    policy.DocumentedRuleDefault(
        name=CERTIFICATE % 'create',
        check_str=RULE_ADMIN_OR_USER_OR_CLUSTER_USER,
        description='Sign a new certificate by the CA.',
        operations=[
            {
                'path': '/v1/certificates',
                'method': 'POST'
            }
        ]
    ),
    policy.DocumentedRuleDefault(
        name=CERTIFICATE % 'get',
        check_str=RULE_ADMIN_OR_USER_OR_CLUSTER_USER,
        description='Retrieve CA information about the given cluster.',
        operations=[
            {
                'path': '/v1/certificates/{cluster_uuid}',
                'method': 'GET'
            }
        ]
    ),
    policy.DocumentedRuleDefault(
        name=CERTIFICATE % 'rotate_ca',
        check_str=base.RULE_ADMIN_OR_OWNER,
        description='Rotate the CA certificate on the given cluster.',
        operations=[
            {
                'path': '/v1/certificates/{cluster_uuid}',
                'method': 'PATCH'
            }
        ]
    )
]


def list_rules():
    return rules
