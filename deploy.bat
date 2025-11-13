@echo off
echo === DSpace Deployment with Kustomize ===
echo.
echo Deploying all components...
kubectl apply -k k8s/
echo.
echo Waiting for components to be ready (this may take 5-8 minutes)...
echo.
echo [1/4] Waiting for PostgreSQL...
kubectl wait --for=condition=ready pod -l cnpg.io/cluster=dspace-postgres -n clarin-dspace-ns --timeout=300s
echo.
echo [2/4] Waiting for Solr...
kubectl wait --for=condition=ready pod -l app=dspace-solr -n clarin-dspace-ns --timeout=300s
echo.
echo [3/4] Waiting for Backend (database initialization may take time)...
kubectl wait --for=condition=ready pod -l app=dspace-backend -n clarin-dspace-ns --timeout=600s
echo.
echo [4/4] Waiting for Angular Frontend...
kubectl wait --for=condition=ready pod -l app=dspace-angular -n clarin-dspace-ns --timeout=300s
echo.
echo === Current Status ===
kubectl get pods -n clarin-dspace-ns
kubectl get services -n clarin-dspace-ns
kubectl get pvc -n clarin-dspace-ns

echo.
echo === Deployment Complete ===
echo.
echo Access your application at:
echo   https://test-hello.dyn.cloud.e-infra.cz/
echo.
echo To scale components independently:
echo   kubectl scale deployment dspace-angular -n clarin-dspace-ns --replicas=2
echo   kubectl scale deployment dspace-backend -n clarin-dspace-ns --replicas=2
echo.
