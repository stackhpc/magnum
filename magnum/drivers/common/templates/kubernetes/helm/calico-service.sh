set +x
. /etc/sysconfig/heat-params
set -ex

CHART_NAME="tigera-operator"

if [ "$NETWORK_DRIVER" = "calico" ]; then
    echo "Writing ${CHART_NAME} config"

    HELM_CHART_DIR="/srv/magnum/kubernetes/helm/magnum"
    mkdir -p ${HELM_CHART_DIR}

    cat << EOF >> ${HELM_CHART_DIR}/requirements.yaml
- name: ${CHART_NAME}
  version: ${CALICO_TAG}
  repository: https://projectcalico.docs.tigera.io/charts
EOF
    cat << EOF >> ${HELM_CHART_DIR}/values.yaml
tigera-operator:
  installation:
    flexVolumePath: /opt/kubernetes/kubelet-plugins/volume/exec/
  flexVolumePluginDir: /var/lib/kubelet/volumeplugins
EOF
fi