set +x
. /etc/sysconfig/heat-params
set -ex

CHART_NAME="tigera-operator"

if [ "$NETWORK_DRIVER" = "calico" ]; then
    echo "Writing ${CHART_NAME} config"

    # HELM_CHART_DIR="/srv/magnum/kubernetes/helm/magnum"
    HELM_CHART_DIR="/srv/magnum/kubernetes/helm/calico"
    mkdir -p ${HELM_CHART_DIR}

#     cat << EOF >> ${HELM_CHART_DIR}/requirements.yaml
# - name: ${CHART_NAME}
#   version: ${CALICO_TAG}
#   repository: https://projectcalico.docs.tigera.io/charts
# EOF
    cat << EOF >> ${HELM_CHART_DIR}/values.yaml
installation:
  flexVolumePath: /opt/kubernetes/kubelet-plugins/volume/exec/
  calicoNetwork:
    ipPools:
    - blockSize: 26
      cidr: ${CALICO_IPV4POOL}
      ipipMode: ${CALICO_IPV4POOL_IPIP}
    nodeAddressAutodetectionV4:
      cidrs:
        - '${CLUSTER_SUBNET_CIDR}'
flexVolumePluginDir: /var/lib/kubelet/volumeplugins
EOF
  echo "Waiting for Kubernetes API..."
  until  [ "ok" = "$(kubectl get --raw='/healthz')" ]; do
      sleep 5
  done
  kubectl create namespace ${CHART_NAME}

  helm_prepare_cmd="helm repo add projectcalico https://docs.tigera.io/calico/charts"
  helm_install_cmd="helm upgrade --install calico projectcalico/tigera-operator --version ${CALICO_TAG} -f values.yaml --namespace tigera-operator"
  helm_history_cmd="helm history calico --namespace tigera-operator"

  if [[ -d "${HELM_CHART_DIR}" ]]; then
      pushd ${HELM_CHART_DIR}
      $helm_prepare_cmd
      i=0
      until ($helm_history_cmd | grep calico | grep deployed) || $helm_install_cmd; do
          i=$((i + 1))
          [ $i -lt 60 ] || break;
          sleep 5
      done
      popd
  fi

fi