pipeline {
    agent any

    environment {
        AWS_REGION      = 'ap-south-1'
        AWS_ACCOUNT_ID  = '379220350808'
        IMAGE_REGISTRY  = '379220350808.dkr.ecr.ap-south-1.amazonaws.com'   // matches image_registry in inventory/group_vars/all/main.yml
        IMAGE_NAME      = 'oan-frappe'   // matches frappe_image_repo — must exist as an ECR repository first
        RKE2_NODE       = '13.233.56.204'      
        K8S_NAMESPACE   = 'develop'
        // All six Deployments run the SAME oan-frappe image with different
        // commands (confirmed via `kubectl get deployment ... -o jsonpath`):
        //   backend       -> gunicorn frappe.app:application
        //   frontend      -> nginx-entrypoint.sh
        //   scheduler     -> bench schedule
        //   websocket     -> node apps/frappe/socketio.js
        //   worker-long   -> bench worker --queue long
        //   worker-short  -> bench worker --queue short,default
       
        DEPLOYMENTS     = 'backend frontend scheduler websocket worker-long worker-short'
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                // oan-frappe is a
                // frappe_docker build - frappe/frappe_docker's own
                // Containerfile, fed an apps.json listing frappe +
                // oan_auth_service + oan_grievance_service as build-time
                // `bench get-app` sources. 
            }
        }

        stage('Prepare apps.json') {
            when { branch 'develop' }
            steps {
                script {
                    // The ops repo's scripts/ansible/files/apps.json pins
                    // oan_auth_service/oan_grievance_service to v0.1.0 - a
                    // release reference, not what a `develop` CI build wants.
                    // Both apps are pinned to env.BRANCH_NAME here instead, so
                    // the image always carries current develop HEAD of BOTH
                    // apps, regardless of which repo's webhook triggered this
                    // build. frappe itself stays on version-16, matching
                    // ci/build-frappe-image.yml.
                    def appsJson = groovy.json.JsonOutput.toJson([
                        [url: 'https://github.com/frappe/frappe', branch: 'version-16'],
                        [url: 'https://github.com/Centre-for-Open-Societal-Systems/oan_auth_service', branch: env.BRANCH_NAME],
                        [url: 'https://github.com/Centre-for-Open-Societal-Systems/oan_grievance_service', branch: env.BRANCH_NAME],
                    ])
                    writeFile file: 'apps.json', text: appsJson
                    env.APPS_JSON_BASE64 = sh(script: 'base64 -w0 apps.json', returnStdout: true).trim()
                }
            }
        }

        stage('Build image') {
            when { branch 'develop' }
            steps {
                script {
                    env.IMAGE_TAG_BUILD = "develop-${env.BUILD_NUMBER}"
                }
                // Mirrors ci/build-frappe-image.yml: frappe_docker's own
                // Containerfile is the build definition, not anything in this
                // repo - this repo only supplies the apps.json branch pins
                // above. NOTE: this is a heavy build (compiles wkhtmltopdf,
                // node, bench build assets for two apps) - confirm which
                // Jenkins agent runs this. 
                
                sh """
                    rm -rf frappe_docker
                    git clone --depth 1 https://github.com/frappe/frappe_docker.git
                    cd frappe_docker
                    docker build \
                        --build-arg FRAPPE_PATH=https://github.com/frappe/frappe \
                        --build-arg FRAPPE_BRANCH=version-16 \
                        --build-arg APPS_JSON_BASE64=${APPS_JSON_BASE64} \
                        --file images/layered/Containerfile \
                        -t ${IMAGE_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG_BUILD} \
                        -t ${IMAGE_REGISTRY}/${IMAGE_NAME}:develop \
                        .
                """
            }
        }

        stage('Push image') {
            when { branch 'develop' }
            steps {
               
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: 'aws-ecr-creds'
                ]]) {
                    sh """
                        aws ecr get-login-password --region ${AWS_REGION} \
                            | docker login --username AWS --password-stdin ${IMAGE_REGISTRY}
                        docker push ${IMAGE_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG_BUILD}
                        docker push ${IMAGE_REGISTRY}/${IMAGE_NAME}:develop
                    """
                }
            }
        }

        stage('Deploy to develop (kubectl)') {
            when { branch 'develop' }
            steps {
                // NOTE: like grievance-ui, frappe_image_tag in
                // inventory/group_vars/nonprod.yml is the fixed string
                // "develop" — a moving tag. Pushing to :develop above doesn't
                // change any Deployment's image reference, so a rollout
                // restart is used instead of `kubectl set image`. This ONLY
                // re-pulls if imagePullPolicy is Always on all six
                // Deployments 
                withCredentials([sshUserPrivateKey(
                    credentialsId: 'grievance-dev-ssh-key',   // grievance.pem
                    keyFileVariable: 'SSH_KEY',
                    usernameVariable: 'SSH_USER'
                )]) {
                    sh """
                        ssh -o StrictHostKeyChecking=no -i \$SSH_KEY \$SSH_USER@${RKE2_NODE} bash -s < /dev/null <<'ENDSSH'
set -euo pipefail
export KUBECONFIG="\$HOME/.kube/config"
for d in ${DEPLOYMENTS}; do
  kubectl rollout restart deployment/\$d -n ${K8S_NAMESPACE}
done
for d in ${DEPLOYMENTS}; do
  echo "waiting on \$d..."
  kubectl rollout status deployment/\$d -n ${K8S_NAMESPACE} --timeout=180s
done
ENDSSH
                    """
                }
            }
        }

        stage('Verify') {
            when { branch 'develop' }
            steps {
                sh '''
                    code=$(curl -sk -o /dev/null -w "%{http_code}" https://grievance-backend-dev.oanstaging.com/)
                    echo "frappe backend responded: $code"
                    [ "$code" = "200" ] || [ "$code" = "302" ]
                '''
               
                // frontend:8080 (Frappe's own nginx/gunicorn stack)
            }
        }
    }

    post {
        failure {
            echo """
                oan-frappe develop deploy failed — check the stage logs above.
                Since backend/websocket/scheduler/worker-long/worker-short/frontend
                all share this one image, a failure here can leave some
                Deployments on the new code and others on the old one if the
                rollout loop was interrupted partway through. Check
                `kubectl get deployment -n develop -o wide` for a mismatched
                image across the six before re-running.
            """
        }
    }
}