pipeline {
    agent any
    stages {
        stage('test') {
            steps {
                sh 'python qpid-python-test -i "*ErrorCallbackTests*" -i "*SelectorTests*" -i "*SetupTests*"'
            }
        }
    }
}
