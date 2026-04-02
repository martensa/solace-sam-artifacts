docker build -t martensa/sam-geo-information-agent:latest .
docker login localhost:5000
docker tag martensa/sam-geo-information-agent:latest localhost:5000/sam-geo-information-agent:latest
docker push localhost:5000/sam-geo-information-agent:latest

kubectl apply -f sam-geo-information-agent-secret.yaml
kubectl apply -f sam-geo-information-agent-config.yaml
kubectl apply -f sam-geo-information-agent-deployment.yaml