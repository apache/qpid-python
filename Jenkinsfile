pipeline {
    agent any
    stages {
        stage('build') {
            steps {
                sh 'python setup.py build'
            }
        }
        stage('test') {
            steps {
                sh 'python qpid-python-test -i "*ErrorCallbackTests*" -i "*SelectorTests*" -i "*SetupTests*"'
            }
        }
        stage('install') {
            steps {
                sh 'python setup.py install --prefix install'
            }
        }
    }
}
