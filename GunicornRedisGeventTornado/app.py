'''
Created on Jun 26, 2012

@author: pedro.vicente
'''
import sys
from gevent import monkey

if not 'tornado' in sys.modules:
    monkey.patch_all()
    import gevent.queue as Queue
else:
    import Queue

from gevent.baseserver import _tcp_listener
from gevent import pywsgi
from gevent import wsgi
from multiprocessing import Process
from optparse import OptionParser
from setproctitle import setproctitle
import redis,os
from redis.exceptions import ConnectionError
from contextlib import contextmanager
from urlparse import urlparse

class ConnectionPool(object):
    "Generic connection pool"
    def __init__(self, connection_class=redis.Connection, max_connections=None,
                 **connection_kwargs):
        self.pid = os.getpid()
        self.connection_class = connection_class
        self.connection_kwargs = connection_kwargs
        self.max_connections = max_connections or 1000 # anything more is useless
        self._connections = []
        self._available_connections = Queue.LifoQueue()
        self._in_use_connections = set()
        for _ in xrange(self.max_connections):
            connection = self.connection_class(**self.connection_kwargs)
            self._connections.append(connection)
            self._available_connections.put(connection)

    def _checkpid(self):
        if self.pid != os.getpid():
            self.disconnect()
            self.__init__(self.connection_class, self.max_connections, **self.connection_kwargs)

    def get_connection(self, command_name, *keys, **options):
        "Get a connection from the pool"
        self._checkpid()
        try:
            connection = self._available_connections.get(block=options.get("block", True))
            self._in_use_connections.add(connection)
            return connection
        except Queue.Empty:
            raise ConnectionError("No connection available.")

    def release(self, connection):
        "Releases the connection back to the pool"
        self._in_use_connections.remove(connection)
        self._available_connections.put(connection)

    def disconnect(self):
        "Disconnects all connections in the pool"
        for connection in self._connections:
            connection.disconnect()


RedisPool = None
Redis_key='connections'
Redis_url = urlparse(os.getenv('REDISTOGO_URL', 'redis://redis:6379'))
assert Redis_url.scheme == 'redis'
Data = 'Hello world!!!'

if not 'gunicorn' in sys.modules:
    parser = OptionParser()
    parser.add_option("-w", "--workers", dest="workers", default=1, type="int", help="Number of worker processes. Default 1")
    parser.add_option("-p", "--pywsgi", dest="pywsgi", action="store_true", default=False,  help="Use gevent.pywsgi")
    parser.add_option("-r", "--redisconnections", dest="redis_connections", default=0, type="int", help="Number of redis connections")
    (options, args) = parser.parse_args()
    if options.redis_connections > 0:
        RedisPool=ConnectionPool(max_connections = options.redis_connections,host=Redis_url.hostname, port=Redis_url.port)

def app(environ, start_response):
    response_headers = [("Content-Type", "text/plain"), ("Content-Length", str(len(Data)))]
    start_response("200 OK", response_headers)
    return iter([Data])

def app_redis(environ, start_response):
    transaction = False
    value = None
    while not transaction:
        r = redis.Redis(connection_pool=RedisPool)
        try:
            value=r.incr(Redis_key)
            transaction = True
        except ConnectionError:
            print "Exception connection Error"
            pass

    local_data = "%s. connection %s" %(Data,value)
    response_headers = [("Content-Type", "text/plain"), ("Content-Length", str(len(local_data)))]
    start_response("200 OK", response_headers)
    return iter([local_data])

def build_app(redisconn=0):
    global RedisPool
    max_conn = int(redisconn)
    if max_conn==0:
        RedisPool = None
        return app
    RedisPool = ConnectionPool(max_connections = max_conn,host=Redis_url.hostname, port=Redis_url.port)
    return app_redis

def serve_forever(listener, procname=''):
    setproctitle(procname)
    myapp = app
    if RedisPool:
        myapp = app_redis
    wsgi_tech.WSGIServer(listener, myapp, log=None).serve_forever()

if __name__ == '__main__':
    import os
    port=os.getenv('PORT', 5000)
    listener = _tcp_listener( ('0.0.0.0', int(port)) )
    wsgi_tech = pywsgi if options.pywsgi else wsgi
    default_title=sys.argv[0].replace('./','')
    tech_string = ' [pywsgi]' if options.pywsgi else ' [wsgi]'
    for i in xrange(options.workers):
        title = default_title + tech_string + ' [worker]'
        Process(target=serve_forever, args=(listener, title)).start()
    setproctitle(default_title + tech_string + ' [master]')
