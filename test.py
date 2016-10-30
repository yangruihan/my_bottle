#!/usr/bin/env python3
# -*- coding:utf-8 -*-


from my_bottle import route, run, request, response, send_file, abort, PasteServer


@route('/')
def hello_world():
    return 'Hello World!'


@route('/hello/:name')
def hello_name(name):
    return 'Hello %s!' % name


@route('/hello', method='POST')
def hello_post():
    name = request.POST['name']
    return 'Hello %s!' % name


@route('/static/:filename#.*#')
def static_file(filename):
    send_file(filename, root='/path/to/static/files/')


run(host='localhost', port=8080)
