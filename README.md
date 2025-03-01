# Kube Cost Application for EKS Cluster

- Python Based application to get Cost Details of Kubernetes Workloads.
- Works like a cluster level agent / exporter
- Can be used to generate json data Or as a prometheus exporter. 
- Json data can be exported to tools like AWS insights / tableu / snowflake.

![Alt text](EKS-Cost-App.png?raw=true "EKS Cost App")

Steps to build, deploy and implement -

1) Basic setup

```
cd kube-cost-base
kubectl apply -f kube-cost-app-base.yaml
kubectl apply -f kube-load-app.yaml                ### Optional : Sample Load Application
```

2) Setup cost app:

Setup AWS role (arn:aws:iam::{aws-account}:role/aws-cost-role) policy-

```
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VisualEditor0",
            "Effect": "Allow",
            "Action": [
                "pricing:DescribeServices",
                "pricing:GetProducts"
            ],
            "Resource": "*"
        }
    ]
}
```

Trust Policy:

```
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Federated": "arn:aws:iam::<aws-account>:oidc-provider/oidc.eks.eu-west-1.amazonaws.com/id/<oidcid>"
            },
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {
                "StringEquals": {
                    "oidc.eks.eu-west-1.amazonaws.com/id/<oidcid>:sub": "system:serviceaccount:costapp:eks-kube-resource-server-sa",
                    "oidc.eks.eu-west-1.amazonaws.com/id/<oidcid>:aud": "sts.amazonaws.com"
                }
            }
        }
    ]
}
```

```
cd kube-cost-server
docker buildx build --platform linux/amd64,linux/arm64 -t <dockerhub-account>/eks-kube-resource-server:latest --push .
kubectl apply -f kube_resource_server.yaml
```

3) Setup cost agent app

```
cd kube-cost-application
docker buildx build --platform linux/amd64,linux/arm64 -t <dockerhub-account>/pod-resource-collector:latest --push .
kubectl apply -f kube-cost-collector-app.yaml
```



Example implementation:

Following are grafana queries
```
sort_desc(sum by (pod_namespace) (pod_wastage_cost{eks_cluster_name="eks-cluster"}))
sort_desc(sum by (pod_namespace) (pod_usage_cost{eks_cluster_name="eks-cluster"}))
```


Benefits of the Solution:
1. Data Privacy: The in-house solution ensures complete control and privacy of the data.
2. Enhanced Security: By eliminating the need for third-party API calls from open-source tools, the solution reduces potential security risks associated with external dependencies.
3. Seamless Integration and tools extension: The solution extends the capabilities of existing tools like Prometheus and Grafana for visualization, requiring only minimal additional resources.
4. Cost Efficiency: No licensing expenses.
5. Ease of Deployment: As illustrated in the flow chart, the implementation is straightforward. We only need to deploy an agent application per cluster, while the 'server' application can be hosted as a shared central component.
