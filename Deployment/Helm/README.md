# Helm Deployment

Link to Solace Agent Mesh - Helm Chart: [GitHub Pages](https://solaceproducts.github.io/solace-agent-mesh-helm-quickstart/docs/)

## Import Images in Local Docker Registry
```
docker login localhost:5000
docker load -i Downloads/solace-agent-mesh-enterprise-1.14.11-arm64.tar.gz
docker tag solace-agent-mesh-enterprise:1.14.11 localhost:5000/solace-agent-mesh-enterprise:1.14.11
docker push localhost:5000/solace-agent-mesh-enterprise:1.14.11
docker load -i Downloads/sam-agent-deployer-1.1.3-arm64.tar.gz
docker tag sam-agent-deployer:1.1.3 localhost:5000/sam-agent-deployer:1.1.3
docker push localhost:5000/sam-agent-deployer:1.1.3
```

## Installation
```
helm repo add solace-agent-mesh https://solaceproducts.github.io/solace-agent-mesh-helm-quickstart/
helm repo update
kubectl create namespace sam-ent-k8s
kubectl create secret docker-registry localhost-registry-secret --docker-server=localhost:5000 --docker-username=registry --docker-password=registry -n sam-ent-k8s
helm install agent-mesh solace-agent-mesh/solace-agent-mesh -f Downloads/local-k8s-values.yaml --namespace sam-ent-k8s
helm status agent-mesh --namespace sam-ent-k8s
kubectl get pods -l app.kubernetes.io/instance=agent-mesh -n sam-ent-k8s
```

## Delete
```
helm uninstall agent-mesh --namespace sam-ent-k8s
kubectl delete pvc -l app.kubernetes.io/instance=agent-mesh -n sam-ent-k8s
kubectl delete namespace sam-ent-k8s
```

## Upgrade
```
helm repo update solace-agent-mesh
helm upgrade agent-mesh solace-agent-mesh/solace-agent-mesh -n sam-ent-k8s --reuse-values --set samDeployment.image.tag=1.65.45 --set samDeployment.agentDeployer.image.tag=1.6.3
```
(helm get values agent-mesh -n <namespace> > current-values.yaml)