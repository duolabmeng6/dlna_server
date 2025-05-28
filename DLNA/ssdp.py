import sys
import random
import socket
import logging
import threading
import cherrypy
from email.utils import formatdate

from .utils import Setting

SSDP_PORT = 1900
SSDP_ADDR = '239.255.255.250'
SERVER_ID = 'SSDP Server'
logger = logging.getLogger("SSDPServer")


class Sock:
    def __init__(self, ip):
        self.ip = ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ssdp_addr = socket.inet_aton(SSDP_ADDR)
        self.interface = socket.inet_aton(self.ip)
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, self.interface)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, self.ssdp_addr + self.interface)
        except Exception as e:
            logger.error(e)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
        # self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 10)

    def send_it(self, response, destination):
        try:
            self.sock.sendto(response.format(self.ip).encode(), destination)
        except (AttributeError, socket.error) as msg:
            logger.warning("发送数据失败：从 {} 到 {}".format(self.ip, destination))

    def close(self):
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP,  self.ssdp_addr + self.interface)
        except Exception:
            pass
        self.sock.close()


class SSDPServer:
    """实现SSDP服务器的类。当服务器接收到适当类型的数据报时，
    会调用notify_received和searchReceived方法。"""
    known = {}

    def __init__(self):
        self.ip_list = []
        self.sock_list = []
        self.sock = None
        self.running = False
        self.ssdp_thread = None
        self.sending_byebye = True
        # 当IP发生变化时，我们需要重启SSDP线程
        # 但我们不希望SSDP发送任何byebye数据

    def start(self):
        """启动SSDP后台线程
        """
        if not self.running:
            self.running = True
            self.sending_byebye = True
            self.ssdp_thread = threading.Thread(target=self.run, name="SSDP_THREAD")
            self.ssdp_thread.start()

    def stop(self, byebye=True):
        """停止SSDP后台线程
        """
        if self.running:
            self.running = False
            # 唤醒套接字，这将加快SSDP线程的退出速度
            try:
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'', (SSDP_ADDR, SSDP_PORT))
            except Exception as e:
                pass
            self.sending_byebye = byebye
            if self.ssdp_thread is not None:
                self.ssdp_thread.join()

    def run(self):
        # 创建UDP服务器
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # 设置IP_MULTICAST_LOOP为false
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)

        # 设置SO_REUSEADDR或SO_REUSEPORT
        if sys.platform == 'win32':
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        elif sys.platform == 'darwin':
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        elif hasattr(socket, "SO_REUSEPORT"):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                logger.debug("SSDP设置SO_REUSEPORT")
            except socket.error as e:
                logger.error("SSDP无法设置SO_REUSEPORT")
                logger.error(str(e))
        elif hasattr(socket, "SO_REUSEADDR"):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                logger.debug("SSDP设置SO_REUSEADDR")
            except socket.error as e:
                logger.error("SSDP无法设置SO_REUSEADDR")
                logger.error(str(e))

        # self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 10)

        self.ip_list = list(Setting.get_ip())
        if sys.platform == 'win32':
            self.ip_list.append(('192.168.137.1', '255.255.255.0'))
        self.sock_list = []
        for ip, mask in self.ip_list:
            try:
                logger.info('添加成员 {}'.format(ip))
                mreq = socket.inet_aton(SSDP_ADDR) + socket.inet_aton(ip)
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                self.sock_list.append(Sock(ip))
            except Exception as e:
                logger.error(e)

        try:
            self.sock.bind(('0.0.0.0', SSDP_PORT))
        except Exception as e:
            logger.error(e)
            cherrypy.engine.publish("app_notify", "DLNA", "SSDP无法启动")
            threading.Thread(target=lambda: Setting.stop_service(), name="SSDP_STOP_THREAD").start()
            return
        self.sock.settimeout(1)

        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                self.datagram_received(data, addr)
            except socket.timeout:
                continue
        self.shutdown()
        for ip, mask in self.ip_list:
            logger.info("移除成员 {}".format(ip))
            mreq = socket.inet_aton(SSDP_ADDR) + socket.inet_aton(ip)
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
            except Exception:
                continue
        self.sock.close()
        self.sock = None

    def shutdown(self):
        for st in self.known:
            self.do_byebye(st)
        usn = [st for st in self.known]
        for st in usn:
            self.unregister(st)

    def datagram_received(self, data, host_port):
        """处理接收到的多播数据报"""

        (host, port) = host_port

        try:
            header = data.decode().split('\r\n\r\n')[0]
        except ValueError as err:
            logger.error(err)
            return
        if len(header) == 0:
            return

        lines = header.split('\r\n')
        cmd = lines[0].split(' ')
        lines = map(lambda x: x.replace(': ', ':', 1), lines[1:])
        lines = filter(lambda x: len(x) > 0, lines)

        headers = [x.split(':', 1) for x in lines]
        headers = dict(map(lambda x: (x[0].lower(), x[1]), headers))

        if cmd[0] != 'NOTIFY':
            logger.info('SSDP命令 %s %s - 来自 %s:%d' %
                        (cmd[0], cmd[1], host, port))
        if cmd[0] == 'M-SEARCH' and cmd[1] == '*':
            # SSDP发现
            logger.debug('M-SEARCH *')
            logger.debug(data)
            self.discovery_request(headers, (host, port))
        elif cmd[0] == 'NOTIFY' and cmd[1] == '*':
            # SSDP存在通知
            # logger.debug('NOTIFY *')
            pass
        else:
            logger.warning('未知的SSDP命令 %s %s' % (cmd[0], cmd[1]))

    def register(self, usn, st, location, server=SERVER_ID,
                 cache_control='max-age=1800'):
        """注册此SSDP服务器将响应的服务或设备"""

        logging.info('注册 %s (%s)' % (st, location))

        self.known[usn] = {}
        self.known[usn]['USN'] = usn
        self.known[usn]['LOCATION'] = location
        self.known[usn]['ST'] = st
        self.known[usn]['EXT'] = ''
        self.known[usn]['SERVER'] = server
        self.known[usn]['CACHE-CONTROL'] = cache_control

    def unregister(self, usn):
        logger.info("Un-registering %s" % usn)
        del self.known[usn]

    def is_known(self, usn):
        return usn in self.known

    def send_it(self, response, destination):
        for sock in self.sock_list:
            sock.send_it(response, destination)

    def get_subnet_ip(self, ip, mask):
        a = [int(n) for n in mask.split('.')]
        b = [int(n) for n in ip.split('.')]
        return [a[i] & b[i] for i in range(4)]

    def discovery_request(self, headers, host_port):
        """Process a discovery request.  The response must be sent to
        the address specified by (host, port)."""

        (host, port) = host_port

        # 检查 'st' 字段是否存在，如果不存在则使用默认值 'ssdp:all'
        search_target = headers.get('st', 'ssdp:all')
        logger.info('Discovery request from (%s,%d) for %s' % (host, port, search_target))

        # 检查 'mx' 字段是否存在，如果不存在则使用默认值 3
        max_delay = int(headers.get('mx', '3'))

        # Do we know about this service?
        for i in self.known.values():
            if i['ST'] == search_target or search_target == 'ssdp:all':
                response = ['HTTP/1.1 200 OK']

                usn = None
                for k, v in i.items():
                    if k == 'USN':
                        usn = v
                    response.append('%s: %s' % (k, v))

                if usn:
                    response.append('DATE: %s' % formatdate(timeval=None,
                                                            localtime=False,
                                                            usegmt=True))

                    response.extend(('', ''))
                    delay = random.randint(0, max_delay)
                    destination = (host, port)
                    logger.debug('send discovery response delayed by %ds for %s to %r' % (delay, usn, destination))

                    for ip, mask in self.ip_list:
                        if self.get_subnet_ip(ip, mask) == self.get_subnet_ip(host, mask):
                            self.sock.sendto('\r\n'.join(response).format(ip).encode(), destination)
                            break

    def do_notify(self, usn):
        """Do notification"""
        logger.debug('Sending alive notification for %s' % usn)

        if usn not in self.known:
            return

        resp = [
            'NOTIFY * HTTP/1.1',
            'HOST: %s:%d' % (SSDP_ADDR, SSDP_PORT),
            'NTS: ssdp:alive',
        ]
        stcpy = dict(self.known[usn].items())
        stcpy['NT'] = stcpy['ST']
        del stcpy['ST']

        resp.extend(map(lambda x: ': '.join(x), stcpy.items()))
        resp.extend(('', ''))
        try:
            self.send_it('\r\n'.join(resp), (SSDP_ADDR, SSDP_PORT))
            self.send_it('\r\n'.join(resp), (SSDP_ADDR, SSDP_PORT))
        except (AttributeError, socket.error) as msg:
            logger.warning("failure sending out alive notification: %r" % msg)

    def do_byebye(self, usn):
        """Do byebye"""
        if not self.sending_byebye:
            return

        logger.info('Sending byebye notification for %s' % usn)

        resp = [
            'NOTIFY * HTTP/1.1',
            'HOST: %s:%d' % (SSDP_ADDR, SSDP_PORT),
            'NTS: ssdp:byebye',
        ]
        try:
            stcpy = dict(self.known[usn].items())
            stcpy['NT'] = stcpy['ST']
            del stcpy['ST']

            resp.extend(map(lambda x: ': '.join(x), stcpy.items()))
            resp.extend(('', ''))
            if self.sock:
                try:
                    self.send_it('\r\n'.join(resp), (SSDP_ADDR, SSDP_PORT))
                except (AttributeError, socket.error) as msg:
                    logger.error("error sending byebye notification: %r" % msg)
        except KeyError as msg:
            logger.error("error building byebye notification: %r" % msg)
