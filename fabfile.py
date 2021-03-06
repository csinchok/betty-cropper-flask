from fabric.contrib.project import rsync_project
from fabric.api import *

LOCAL_DIR = '/tmp/betty_deploy/'

env.user = 'ansible'
env.webroot = '/www/betty-cropper/'
env['disable_known_hosts'] = True

def archive():
    local('mkdir -p %s' % LOCAL_DIR)
    local('git archive HEAD | tar -x -C %s' % LOCAL_DIR, capture=False)

def push():
    try:
        rsync_project(env.webroot, local_dir=LOCAL_DIR, delete=True, extra_opts='-q -l', exclude=["bin", "lib", "include", "settings.py"])
    except Exception, e:
        print "*** Exception during sync:", e

def restart():
    run('sudo /etc/init.d/betty-cropper restart')

def clean():
    local("rm -r %s" % LOCAL_DIR)

@hosts('img.onionstatic.com')
def deploy():
    archive()
    push()
    restart()
    clean()