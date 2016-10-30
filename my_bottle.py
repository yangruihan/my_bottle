#!/usr/bin/env python3
# -*- coding:utf-8 -*-

from urllib import parse
import cgi
import mimetypes
import os
import sys
import traceback
import re
import random
import http.cookies
import threading
import time

try:
    from urlparse import parse_qs
except ImportError:
    from cgi import parse_qs

DEBUG = False
OPTIMIZER = False
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


# 类定义

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
        for key, values in dict.items(self):
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

    @property
    def query_string(self):
        """
        查询字段的内容
        """
        return self._environ.get('QUERY_STRING', '')

    @property
    def input_length(self):
        """
        内容长度
        """
        try:
            return int(self._environ.get('CONTENT_LENGTH', '0'))
        except ValueError:
            return 0

    @property
    def GET(self):
        """
        返回 GET 方法的参数字典
        """
        if self._GET is None:
            raw_dict = parse.parse_qs(self.query_string, keep_blank_values=1)
            self._GET = {}
            for key, value in raw_dict.items():
                if len(value) == 1:
                    self._GET[key] = value[0]
                else:
                    self._GET[key] = value
        return self._GET

    @property
    def POST(self):
        """
        返回 POST 方法的参数字典
        """
        if self._POST is None:
            raw_data = cgi.FieldStorage(fp=self._environ['wsgi.input'], environ=self._environ)
            self._POST = {}
            for key in raw_data:
                if raw_data[key].filename:
                    self._POST[key] = raw_data[key]
                elif isinstance(raw_data[key], list):
                    self._POST[key] = [v.value for v in raw_data[key]]
                else:
                    self._POST[key] = raw_data[key].value
        return self._POST

    @property
    def params(self):
        """
        返回 GET POST 混合参数，POST 参数会覆盖掉 GET 里的参数
        """
        if self._GETPOST is None:
            self._GETPOST = dict(self.GET)
            self._GETPOST.update(self.POST)
        return self._GETPOST

    @property
    def COOKIES(self):
        """
        返回 Cookies 字典
        """
        if self._COOKIES is None:
            raw_dict = http.cookies.SimpleCookie(self._environ.get('HTTP_COOKIE', ''))
            self._COOKIES = {}
            for cookie in raw_dict.values():
                self._COOKIES[cookie.key] = cookie.value
        return self._COOKIES


class Response(threading.local):
    """
    使用 thread-local 命名空间来表示一个单独的响应
    """

    def bind(self):
        """
        清除旧数据，创建一个新的响应对象
        """
        self._COOKIES = None
        self.status = 200
        self.header = HeaderDict()
        self.content_type = 'text/html'
        self.error = None

    @property
    def COOKIES(self):
        if not self._COOKIES:
            self._COOKIES = http.cookies.SimpleCookie()
        return self._COOKIES

    def set_cookie(self, key, value, **kargs):
        """
        设置一个 Cookie
        可选设置包括：expires, path, comment, domain, max-age, secure, version, httponly
        """
        self.COOKIES[key] = value
        for k in kargs:
            self.COOKIES[key][k] = kargs[k]

    def get_content_type(self):
        """
        获取 Content-Type 标头内容
        默认为 text/html
        """
        return self.header['Content-Type']

    def set_content_type(self, value):
        self.header['Content-Type'] = value

    content_type = property(get_content_type, set_content_type, None, get_content_type.__doc__)


# 路由方法
def compile_route(route):
    """
    编译路由字符串，返回预编译正则表达式对象

    路由字符串支持 url 参数，通过包含命名组的正则表达式
    例如：
        '/user/(?P<id>[0-9]+)' 将匹配 '/user/5' 并且提取参数 {'id':'5'}

    更可读的语法也是支持的
    例如：
        '/user/:id/:action' 将匹配 '/user/5/kiss' 并且提取参数 {'id':'5', 'action':'kiss'}
        占位符将匹配任何内容知道下一个'/'
        '/user/:id#[0-9]+#' 将匹配 '/user/5' 但不能匹配 '/user/tim'
        除了使用'#'以外，你可以使用任何单独的特殊字符除了'/'
    """
    route = route.strip().lstrip('$^/ ').rstrip('$^ ')
    route = re.sub(r':([a-zA-Z_]+)(?P<uniq>[^\w/])(?P<re>.+?)(?P=uniq)', r'(?P<\1>\g<re>)', route)
    route = re.sub(r':([a-zA-Z_]+)', r'(?P<\1>[^/]+)', route)
    return re.compile('^/%s$' % route)


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


def add_route(route, handler, method='GET', simple=False):
    """
    向路由映射表中添加一个新的路由

    例如：
    def hello():
        return "Hello World!"
    add_route(r'/hello', hello)
    """
    method = method.strip().upper()
    if re.match(r'^/(\w+/)*\w*$', route) or simple:
        ROUTES_SIMPLE.setdefault(method, {})[route] = handler
    else:
        route = compile_route(route)
        ROUTES_REGEXP.setdefault(method, []).append([route, handler])


def route(url, **kargs):
    """
    request 处理器装饰器
    作用与 add_route 相同
    """

    def wrapper(handler):
        add_route(url, handler, **kargs)
        return handler

    return wrapper


# 错误处理
def set_error_handler(code, handler):
    """
    设置一个新的错误处理器
    """
    code = int(code)
    ERROR_HANDLER[code] = handler


def error(code=500):
    """
    错误处理器装饰器
    作用于 set_error_handler 相同
    """

    def wrapper(handler):
        set_error_handler(code, handler)
        return handler

    return wrapper


def WSGIHandler(environ, start_response):
    """
    自定义 WSGI Handler
    :param environ: 环境变量
    :param start_response: 响应
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

    if hasattr(output, 'fileno') and 'Content-Length' not in response.header:
        size = os.fstat(output.filenp()).st_size
        response.header['Content-Length'] = size

    if hasattr(output, 'read'):
        file_output = output
        if 'wsgi.file_wrapper' in environ:
            output = environ['wsgi.file_wrapper'](file_output)
        else:
            output = iter(lambda: file_output.read(8192), '')

    for c in response.COOKIES.values():
        response.header.add('Set-Cookie', c.OutputString())

    status = '%d %s' % (response.status, HTTP_CODES[response.status])
    start_response(status, list(response.header.items()))
    return output


# 服务器适配器
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
        from paste.translogger import TransLogger
        app = TransLogger(handler)
        httpserver.serve(app, host=self.host, port=str(self.port))


# Python3 不支持
# class FapwsServer(ServerAdapter):
#     def run(self, handler):
#         import fapws._evwsgi as evwsgi
#         from fapws import base
#         import sys
#         evwsgi.start(self.host, self.port)
#         evwsgi.set_base_module(base)
#
#         def app(environ, start_response):
#             environ['wsgi.multiprocess'] = False
#             return handler(environ, start_response)
#
#         evwsgi.wsgi_cb(('', app))
#         evwsgi.run()


def run(server=WSGIRefServer, host='127.0.0.1', port=8080, optinmize=False, **kargs):
    global OPTIMIZER

    OPTIMIZER = bool(optinmize)
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


# 辅助方法
def abort(code=500, text='Unknown Error: Application stopped.'):
    """
    中止执行并导致一个 HTTP 错误
    """
    raise HTTPError(code, text)


def redirect(url, code=307):
    """
    中止执行并导致一个 307 重定向
    """
    response.status = code
    response.header['Location'] = url
    raise BreakTheBottle("")


def send_file(filename, root, guessmime=True, mimetype='text/plain'):
    """
    中止执行并发送一个静态文件作为响应
    """
    root = os.path.abspath(root) + '/'
    filename = os.path.normpath(filename).strip('/')
    filename = os.path.join(root, filename)

    if not filename.startswith(root):
        abort(401, "Access denied.")
    if not os.path.exists(filename) or not os.path.isfile(filename):
        abort(404, "File does not exist.")
    if not os.access(filename, os.R_OK):
        abort(401, "You do not have permission to access this file.")

    if guessmime:
        guess = mimetypes.guess_type(filename)[0]
        if guess:
            response.content_type = guess
        elif mimetype:
            response.content_type = mimetype
    elif mimetype:
        response.content_type = mimetype

    stats = os.stat(filename)
    if 'Content-Length' not in response.header:
        response.header['Content-Length'] = stats.st_size

    if 'Last-Modified' not in response.header:
        ts = time.gmtime(stats.st_mtime)
        ts = time.strftime("%a, %d %b %Y %H:%M:%S +0000", ts)
        response.header['Last-Modified'] = ts

    raise BreakTheBottle(open(filename, 'r'))


# 装饰器
def validate(**vkargs):
    def decorator(func):
        def wrapper(**kargs):
            for key in kargs:
                if key not in vkargs:
                    abort(400, 'Missing parameter: %s' % key)
                try:
                    kargs[key] = vkargs[key](kargs[key])
                except ValueError as e:
                    abort(400, 'Wrong parameter format for: %s' % key)
            return func(**kargs)

        return wrapper

    return decorator


# 默认错误处理器
@error(500)
def error500(exception):
    if DEBUG:
        return "<br>\n".join(traceback.format_exc(10).splitlines()).replace('  ', '&nbsp;&nbsp;')
    else:
        return """<b>Error:</b> Internal server error."""


@error(400)
@error(401)
@error(404)
def error_http(exception):
    status = response.status
    name = HTTP_CODES.get(status, 'Unknown').title()
    url = request.path
    yield '<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML 2.0//EN">'
    yield '<html><head><title>Error %d: %s</title>' % (status, name)
    yield '</head><body><h1>Error %d: %s</h1>' % (status, name)
    yield '<p>Sorry, the requested URL %s caused an error.</p>' % url
    if hasattr(exception, 'output'):
        yield exception.output
    yield '</body></html>'


request = Request()
response = Response()
