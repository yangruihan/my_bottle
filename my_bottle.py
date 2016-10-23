#!/usr/bin/env python3
# -*- coding:utf-8 -*-

import cgi
import mimetypes
import os
import re
import random
import http.cookies
import threading

try:
    from urlparse import parse_qs
except ImportError:
    from cgi import parse_qs

DEBUG = False
OPTIMIZER = True
ROUTES_SIMPLE = {}
ROUTES_REGEXP = {}
ERROR_HANDLER = {}
HTTP_CODES = {
    100: 'CONTINUE',
    101: 'SWITCHING PROTOCOLS',
    200: 'OK',
    201: 'CREATED',
    202: 'ACCEPTED',
    203: 'NON-AUTHORITATIVE INFORMATION',
    204: 'NO CONTENT',
    205: 'RESET CONTENT',
    206: 'PARTIAL CONTENT',
    300: 'MULTIPLE CHOICES',
    301: 'MOVED PERMANENTLY',
    302: 'FOUND',
    303: 'SEE OTHER',
    304: 'NOT MODIFIED',
    305: 'USE PROXY',
    306: 'RESERVED',
    307: 'TEMPORARY REDIRECT',
    400: 'BAD REQUEST',
    401: 'UNAUTHORIZED',
    402: 'PAYMENT REQUIRED',
    403: 'FORBIDDEN',
    404: 'NOT FOUND',
    405: 'METHOD NOT ALLOWED',
    406: 'NOT ACCEPTABLE',
    407: 'PROXY AUTHENTICATION REQUIRED',
    408: 'REQUEST TIMEOUT',
    409: 'CONFLICT',
    410: 'GONE',
    411: 'LENGTH REQUIRED',
    412: 'PRECONDITION FAILED',
    413: 'REQUEST ENTITY TOO LARGE',
    414: 'REQUEST-URI TOO LONG',
    415: 'UNSUPPORTED MEDIA TYPE',
    416: 'REQUESTED RANGE NOT SATISFIABLE',
    417: 'EXPECTATION FAILED',
    500: 'INTERNAL SERVER ERROR',
    501: 'NOT IMPLEMENTED',
    502: 'BAD GATEWAY',
    503: 'SERVICE UNAVAILABLE',
    504: 'GATEWAY TIMEOUT',
    505: 'HTTP VERSION NOT SUPPORTED',
}


# 异常和事件
class BottleException(Exception):
    pass


class HTTPError(BottleException):
    """
    终止当前执行程序，立即跳到错误处理器
    """

    def __init__(self, status, text):
        self.output = text
        self.http_status = int(status)

    def __str__(self):
        return self.output


class BreakTheBottle(BottleException):
    """
    不是一个异常，但是会直接跳出控制语句
    由 WSGIHandler 立即调用 start_response() 方法导致
    返回输出内容
    """

    def __init__(self, output):
        self.output = output


class HeaderDict(dict):
    """
    对键值大小写不敏感的字典
    你可以通过添加字符串列表的形式添加多个具有相同名字的标头
    """

    def __setitem__(self, key, value):
        return dict.__setitem__(self, key.title(), value)

    def __getitem__(self, item):
        return dict.__getitem__(self, item.title())

    def __delitem__(self, key):
        return dict.__delitem__(self, key.title())

    def __contains__(self, item):
        return dict.__contains__(self, item.title())

    def items(self):
        """
        返回一个 (key, value) 元组的列表
        """
        for key, values in dict.items():
            if not isinstance(values, list):
                values = [values]
            for value in values:
                yield (key, str(value))

    def add(self, key, value):
        """
        添加一个新的标头，并且不删掉原来的那个
        """
        if isinstance(value, list):
            for v in value:
                self.add(key, v)
        elif key in self:
            if isinstance(self[key], list):
                self[key].append(value)
            else:
                self[key] = [self[key], value]
        else:
            self[key] = [value]


class Request(threading.local):
    """
    使用 thread-local 命名空间来表示一个单独的请求
    """

    def __init__(self):
        self._environ = None
        self._GET = None
        self._POST = None
        self._GETPOST = None
        self._COOKIES = None
        self.path = ''

    def bind(self, environ):
        """
        绑定当前的请求中的环境变量到这个请求处理类中
        :param environ: 环境变量
        """
        self._environ = environ
        self._GET = None
        self._POST = None
        self._GETPOST = None
        self._COOKIES = None
        self.path = self._environ.get('PATH_INFO', '/').strip()
        if not self.path.startswith('/'):
            self.path = '/' + self.path

    @property
    def method(self):
        """
        返回请求方法 （GET, POST, PUT, DELETE, ...）
        """
        return self._environ.get('REQUEST_METHOD', 'GET').upper()


class Response(threading.local):
    """
    使用 thread-local 命名空间来表示一个单独的响应
    """

    def __init__(self):
        self._COOKIES = None
        self.status = 0
        self.header = None
        self.content_type = ''
        self.error = None

    def bind(self):
        """
        清除旧数据，创建一个新的响应对象
        :return:
        """
        self._COOKIES = None

        self.status = 200
        self.header = HeaderDict()
        self.content_type = 'text/html'
        self.error = None


# 路由方法

def match_url(url, method='GET'):
    """
    返回第一个匹配的 Handler 和一个参数字典
    否则抛出 HTTPError(404) 异常

    每隔 1000 个请求重新排列一次 ROUTING_REGEXP 列表
    如果要关闭此功能，使用 OPTIMIZER = False
    """
    url = '/' + url.strip().lstrip("/")

    # 优先在静态路由表中查找
    route = ROUTES_SIMPLE.get(method, {}).get(url, None)
    if route:
        return route, {}

    # 搜索正则表达式路由配置
    routes = ROUTES_REGEXP.get(method, [])
    for i in range(len(routes)):
        match = routes[i][0].match(url)
        if match:
            handler = routes[i][1]
            if i > 0 and OPTIMIZER and random.random() <= 0.001:
                # 每 1000 次请求，将路由匹配列表中的元素与其前驱进行交换
                # 经常使用的线路会逐渐出现在列表前面
                routes[i - 1], routes[i] = routes[i], routes[i - 1]
            return handler, match.groupdict()
    raise HTTPError(404, "Not Found")


def WSGIHandler(environ, start_response):
    """
    自定义 WSGI Handler
    :param environ: 环境变量
    :param start_response: 响应
    :return:
    """
    global request
    global response
    request.bind(environ)
    response.bind()
    try:
        handler, args = match_url(request.path, request.method)
        output = handler(**args)
    except BreakTheBottle as shard:
        output = shard.output
    except Exception as exception:
        response.status = getattr(exception, 'http_status', 500)
        error_handler = ERROR_HANDLER.get(response.status, None)
        if error_handler:
            try:
                output = error_handler(exception)
            except:
                output = 'Exception within error handler! Application stopped.'
        else:
            if DEBUG:
                output = 'Exception %s: %s' % (exception.__class__.__name__, str(exception))
            else:
                output = 'Unhandled exception: Application stopped.'

        if response.status == 500:
            request._environ['wsgi.errors'].write("Error (500) on '%s': %s\n" % (request.path, exception))

    if hasattr(output, 'read'):
        if 'wsgi.file_wrapper' in environ:
            output = environ['wsgi.file_wrapper'](output)
        else:
            output = iter(lambda: output.read(8192), '')

    if hasattr(output, '__len__') and 'Content-Length' not in response.header:
        response.header['Content-Length'] = len(output)

    for c in response.COOKIES.values():
        response.header.add('Set-Cookie', c.OutputString())

    status = '%d %s' % (response.status, HTTP_CODES[response.status])
    start_response(status, list(response))
    return output


class ServerAdapter(object):
    """
    服务器适配器，用于为多个不同的服务器端，提供统一的接口
    """

    def __init__(self, host='127.0.0.1', port=8080, **kargs):
        self.host = host
        self.port = int(port)
        self.options = kargs

    def __repr__(self):
        return "%s (%s:%d)" % (self.__class__.__name__, self.host, self.port)

    def run(self, handler):
        pass


class WSGIRefServer(ServerAdapter):
    def run(self, handler):
        from wsgiref.simple_server import make_server
        srv = make_server(self.host, self.port, handler)
        srv.serve_forever()


class CherryPyServer(ServerAdapter):
    def run(self, handler):
        from cherrypy import wsgiserver
        server = wsgiserver.CherryPyWSGIServer((self.host, self.port), handler)
        server.start()


class FlupServer(ServerAdapter):
    def run(self, handler):
        from flup.server.fcgi import WSGIServer
        WSGIServer(handler, bindAddress=(self.host, self.port)).run()


class PasteServer(ServerAdapter):
    def run(self, handler):
        from paste import httpserver
        httpserver.serve(handler, host=self.host, port=str(self.port))


def run(server=WSGIRefServer, host='127.0.0.1', port=8080, **kargs):
    quiet = bool('quiet' in kargs and kargs['quiet'])

    if isinstance(server, type) and issubclass(server, ServerAdapter):
        server = server(host=host, port=port, **kargs)

    if not isinstance(server, ServerAdapter):
        raise RuntimeError("Server must be a subclass of ServerAdapter")

    if not quiet:
        print('Server starting up (using %s)...' % repr(server))
        print('Listening on http://%s:%d/' % (server.host, server.port))
        print('Use Ctrl-C to quit.')
        print()

    try:
        server.run(WSGIHandler)
    except KeyboardInterrupt:
        print('Shuting down...')


request = Request()
response = Response()
