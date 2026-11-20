pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        // Heavy build (frappe_docker layered image, node + bench build for 2 apps)
        timeout(time: 90, unit: 'MINUTES')
    }

    environment {
        AWS_REGION          = 'ap-south-1'
        AWS_ACCOUNT_ID      = '379220350808'
        IMAGE_REGISTRY      = '379220350808.dkr.ecr.ap-south-1.amazonaws.com'
        GRIEVANCE_NAMESPACE = 'grievance'
        ECR_REPO            = 'eoan/oan-grievance-service/grievance-service'
        ECR_SEED_REPO       = 'eoan/oan-grievance-service/grievance-seed'
        FRAPPE_DOCKER_REF   = 'main'
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                script {
                    if (env.BRANCH_NAME == 'develop') {
                        env.IMMUTABLE_TAG = sh(returnStdout: true, script: 'python3 ci/package_chart.py version').trim()
                    } else {
                        def sha = sh(returnStdout: true, script: 'git rev-parse --short=7 HEAD').trim()
                        env.IMMUTABLE_TAG = "${env.BRANCH_NAME}-${env.BUILD_NUMBER}-${sha}"
                    }
                    env.FULL_IMAGE      = "${env.IMAGE_REGISTRY}/${env.ECR_REPO}:${env.IMMUTABLE_TAG}"
                    env.SEED_IMAGE      = "${env.IMAGE_REGISTRY}/${env.ECR_SEED_REPO}:${env.IMMUTABLE_TAG}"
                    env.FRAPPE_BRANCH   = sh(returnStdout: true, script: 'python3 ci/package_chart.py app-version').trim()

                    def files = sh(returnStdout: true, script: 'git diff --name-only HEAD^1 HEAD || echo apisix/').trim().split('\n')
                    env.GATEWAY_CHANGED = files.any { it.startsWith('apisix/') || it.startsWith('openapi/') || it == 'helm/environments/dev/oan-grievance-gateway.yaml' } ? 'true' : 'false'
                    echo "Branch ${env.BRANCH_NAME} -> tag ${env.IMMUTABLE_TAG}, image ${env.FULL_IMAGE}"
                }
            }
        }

        stage('Prepare apps.json') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                script {
                    def branch = env.BRANCH_NAME
                    def appsJson = """[
  {"url": "https://github.com/Centre-for-Open-Societal-Systems/oan_auth_service", "branch": "${branch}"},
  {"url": "https://github.com/Centre-for-Open-Societal-Systems/oan_grievance_service", "branch": "${branch}"}
]
"""
                    writeFile file: 'apps.json', text: appsJson
                }
            }
        }

        stage('Build image') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                sh '''
                    [ -s "$WORKSPACE/apps.json" ] || { echo "apps.json is missing or empty"; exit 1; }
                    cat "$WORKSPACE/apps.json"

                    rm -rf frappe_docker
                    git clone --depth 1 --branch "$FRAPPE_DOCKER_REF" https://github.com/frappe/frappe_docker.git
                    cd frappe_docker
                    DOCKER_BUILDKIT=1 docker build \
                        --secret id=apps_json,src="$WORKSPACE/apps.json" \
                        --build-arg CACHE_BUST="$BUILD_NUMBER" \
                        --build-arg FRAPPE_PATH=https://github.com/frappe/frappe \
                        --build-arg FRAPPE_BRANCH="$FRAPPE_BRANCH" \
                        --file images/layered/Containerfile \
                        -t "$FULL_IMAGE" \
                        -t "$IMAGE_REGISTRY/$ECR_REPO:develop" \
                        .

                    # Never push or deploy an image that is missing the OAN apps
                    docker run --rm "$FULL_IMAGE" bash -c "ls apps && test -d apps/oan_auth_service && test -d apps/oan_grievance_service"
                '''
            }
        }

        stage('Build seed') {
            when { branch 'develop' }
            steps {
                sh '''
                    # The commit openg2p-data develop points at: an unchanged build-arg would let the
                    # layer cache keep serving an old clone after the data is fixed.
                    OPENG2P_DATA_REF=$(git ls-remote https://github.com/Centre-for-Open-Societal-Systems/openg2p-data.git refs/heads/develop | cut -f1)
                    test -n "${OPENG2P_DATA_REF}" || { echo "Could not resolve openg2p-data develop" >&2; exit 1; }
                    echo "openg2p-data develop is ${OPENG2P_DATA_REF}"
                    DOCKER_BUILDKIT=1 docker buildx build \
                        --build-arg OPENG2P_DATA_REF=${OPENG2P_DATA_REF} \
                        --tag "$SEED_IMAGE" \
                        --load ci/seed
                '''
            }
        }

        stage('Push image') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: 'aws-ecr-creds'
                ]]) {
                    sh '''#!/usr/bin/env bash
                        set -euo pipefail
                        aws ecr get-login-password --region "$AWS_REGION" \
                            | docker login --username AWS --password-stdin "$IMAGE_REGISTRY"

                        # ECR does not create a repository on push.
                        for repo in "$ECR_REPO" "$ECR_SEED_REPO"; do
                            aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$repo" >/dev/null 2>&1 \
                                || aws ecr create-repository --region "$AWS_REGION" --repository-name "$repo" >/dev/null
                        done

                        docker push "$FULL_IMAGE"
                        if [ "${BRANCH_NAME}" = "develop" ]; then
                            docker push "$IMAGE_REGISTRY/$ECR_REPO:develop"
                            docker push "$SEED_IMAGE"
                            docker rmi "$FULL_IMAGE" "$IMAGE_REGISTRY/$ECR_REPO:develop" "$SEED_IMAGE" || true
                        else
                            docker rmi "$FULL_IMAGE" || true
                        fi
                        echo "Pushed images for ${BRANCH_NAME}"
                    '''
                }
            }
        }

        stage('Package chart') {
            when { branch 'develop' }
            steps {
                script {
                    env.IMAGE_TAG        = env.IMMUTABLE_TAG
                    env.SOURCE_COMMIT    = sh(returnStdout: true, script: 'git rev-parse --verify HEAD').trim()
                    env.COMMIT_SUBJECT   = sh(returnStdout: true, script: 'git log -1 --pretty=%s').trim()
                    env.IMAGE_REPOSITORY = "${env.IMAGE_REGISTRY}/${env.ECR_REPO}"
                }
                sh 'python3 ci/package_chart.py package'
                // The deploy stage runs on the VPN deploy agent, which gets the archive from here.
                stash name: 'built-chart', includes: 'built-chart/**'
            }
        }

        // The routes chart (apisix/k8s) is packaged and published like the app chart, but only for a build that
        // changed the spec, the routes or the gateway values: those are the builds that install it.
        stage('Package gateway chart') {
            when {
                beforeAgent true
                allOf {
                    branch 'develop'
                    expression { env.GATEWAY_CHANGED == 'true' }
                }
            }
            steps {
                sh 'python3 ci/package_chart.py gateway-package'
                stash name: 'built-gateway-chart', includes: 'built-gateway-chart/**'
            }
        }

        stage('develop → oan_dev (helm)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl that reaches the oan_dev API
            options { timeout(time: 45, unit: 'MINUTES') }
            steps {
                checkout scm
                unstash 'built-chart'
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'bash ci/deploy-helm-dev.sh'
                }
            }
        }

        // develop -> APISIX routes (chart apisix/k8s) on the oan_dev node, after the app deploy.
        // Runs only when the spec, the generated routes or the gateway values changed.
        stage('develop → gateway (apisix)') {
            when {
                beforeAgent true
                allOf {
                    branch 'develop'
                    expression { env.GATEWAY_CHANGED == 'true' }
                }
            }
            agent { label 'oan-ci-deploy' }
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                checkout scm
                sh 'rm -rf built-gateway-chart'
                unstash 'built-gateway-chart'
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'bash ci/deploy-gateway.sh'
                }
            }
        }

        stage('Verify') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }
            steps {
                sh '''
                    host="grievance-service.development.oanstaging.com"
                    url="https://$host/v1/auth/iam/providers"
                    for i in $(seq 1 10); do
                        code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 "$url" || true)
                        echo "attempt $i: $url -> $code"
                        case "$code" in 2??|401|403) exit 0 ;; esac
                        sleep 6
                    done
                    echo "grievance service did not answer at $url"
                    exit 1
                '''
            }
        }

        stage('Publish chart') {
            when { branch 'develop' }
            steps {
                // UNSTABLE, not failed: the image is already deployed.
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    withCredentials([usernamePassword(credentialsId: 'oan-helm-fork-token', usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
                        sh 'python3 ci/package_chart.py publish'
                    }
                }
            }
        }

        stage('Publish gateway chart') {
            when {
                beforeAgent true
                allOf {
                    branch 'develop'
                    expression { env.GATEWAY_CHANGED == 'true' }
                }
            }
            steps {
                // UNSTABLE, not failed: the routes are already installed.
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    withCredentials([usernamePassword(credentialsId: 'oan-helm-fork-token', usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
                        sh 'python3 ci/package_chart.py gateway-publish'
                    }
                }
            }
        }
    }

    post {
        failure {
            echo "oan-frappe ${env.BRANCH_NAME} deploy failed."
        }
    }
}
