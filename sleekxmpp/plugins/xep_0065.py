"""
Currently under dev by Mendix.com
license to be announced, some type of opensource though
"""

from __future__ import division, with_statement, unicode_literals
import struct
import logging
import threading
import xep_0096
import socket
from hashlib import sha1
from .. basexmpp import DEFAULT_NS
from .. xmlstream.matcher.xpath import MatchXPath
from .. xmlstream.handler.callback import Callback
from sleekxmpp.xmlstream import register_stanza_plugin
from .. xmlstream.stanzabase import ElementBase, ET
from .. stanza.iq import Iq

STREAM_CLOSED_EVENT = 'BYTE_STREAM_CLOSED'
log = logging.getLogger(__name__)

def sendAckIQ(xmpp, to, id):
    iq = xmpp.makeIqResult(id=id)
    iq['to'] = to
    iq.send()
    
def sendCloseStream(xmpp, to, sid):
    close = ET.Element('{%s}close' %xep_0065.XMLNS, sid=sid)
    iq = xmpp.makeIqSet()
    iq['to'] = to
    iq.setPayload(close)
    iq.send()
    

class xep_0065(xep_0096.FileTransferProtocol):
    XMLNS = 'http://jabber.org/protocol/bytestreams'
    SOCKET_NEGOTIATE_XPATH = MatchXPath('{%s}iq/{%s}query' % (DEFAULT_NS, XMLNS))
       
    def plugin_init(self):
        self.xep = '0065'
        self.description = 'Socks5 Bytestreams'
        self.incoming_files = {}

        register_stanza_plugin(Iq, StreamHosts)
        register_stanza_plugin(Iq, StreamHostUsed)

        socket_negotiation_callback = Callback('negotiate_file_transfer_socket', xep_0065.SOCKET_NEGOTIATE_XPATH, self._handle_socket_negotiation)

        self.xmpp.register_handler(socket_negotiation_callback)
        
    def post_init(self):
        xep_0096.FileTransferProtocol.post_init(self)
        if self.xmpp.plugin.get('xep_0030'):
            self.xmpp.plugin['xep_0030'].add_feature(xep_0065.XMLNS)
        
    def sendFile(self, fileName, to, threaded=True, sid=None):
        pass
    
    def getSessionStatus(self, sid):
        None
        
    def getSessionStatusAll(self):
        return {}
        
    def cancelSend(self, sid):
        pass
            
    def start_receive_file(self, file_name, file_length, sid):
      self.incoming_files[sid] = {"name" : file_name, "length" : file_length}
      log.debug("A file was announced to me: %s " % file_name)

    def _handle_socket_negotiation(self, iq):
      sid = iq['streamhosts']['sid']
      host = None
      port = None
      jid = None
      requester_jid = iq['from']
      target_jid = iq['to']

      file_size = int(self.incoming_files[sid]['length'])
      byte_stream_session = ByteStreamSession(self.xmpp, sid, requester_jid, target_jid, file_size)

      for (jid_key, s) in iq['streamhosts']['hosts'].iteritems():
        host = s['host']
        port = s['port']
        jid = jid_key
        try:
          byte_stream_session.open_socket(host, port)
          break
        except Exception:
          pass

      if byte_stream_session.connected:
        self._socket_connected_ack(iq, jid)
        byte_stream_session.start()
      else:
        log.error("Couldn't open socket for filetransfer %s" % sid)
        self._socket_connected_nack(iq, jid)

    def _socket_connected_ack(self, request_iq, jid):
        query = ET.Element('{%s}query' % xep_0065.XMLNS)
        streamhost_used = ET.SubElement(query, 'streamhost-used')
        streamhost_used.set('jid', jid)
        
        request_iq.reply().set_payload(query)
        request_iq.send()
        
    def _socket_connected_nack(self, request_iq, jid):
        error = ET.Element('error', type='cancel')
        ET.SubElement(error, '{urn:ietf:params:xml:ns:xmpp-stanzas}remote-server-not-found')
        
        request_iq.reply().set_payload(error)
        request_iq.send()

class ByteStreamSession(threading.Thread):
    
    def __init__(self, xmpp, sid, requester_jid, target_jid,  file_length):
      threading.Thread.__init__(self, name='bytestream_session_%s' % sid )

      self.address_digest = sha1("%s%s%s" % (sid, requester_jid, target_jid)).hexdigest()

      self.file_length = file_length
      self.connected = False
      log.debug("Initialized bytestreamsession")
        
    def run(self):
      log.debug("running...")
      self.receive_file()

    def open_socket(self, host, port):
      log.debug("Going to try and open a socket at %s:%s" % (host, port))
      port = int(port)
      self.clientsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      self.clientsocket.settimeout(5.0)
      
      self.clientsocket.connect((host, port))
      self.file_socket = self.clientsocket.makefile()

      self._send_socks_auth_methods_request()
      self._read_socks_auth_methods_response()

      self._send_socks_relay_request()
      self._read_socks_relay_response()
      self.connected = True

      log.debug("socket connected at %s:%s" % (host, port))

    def receive_file(self):
      destination = "/tmp/botje.jpg"
      outputfile = open(destination, "wb")
      
      amount_received = 0
      
      try:
        while amount_received<self.file_length:
          chunk = self.clientsocket.recv(self.file_length-amount_received)
          amount_received = amount_received + len(chunk)

          if len(chunk) == 0:
            raise RuntimeError("socket connection broken")

          outputfile.write(chunk)
      except socket.error, msg:
        log.error("something went horribly wrong %s" % msg)


      outputfile.flush()
      outputfile.close()

      self.clientsocket.close()
      log.debug("file saved!")

    def _send_socks_relay_request(self):
      digest = '!BBBBB%dsBB' % len(self.address_digest)

      relay_request = struct.pack(str(digest), 0x05, 0x01, 0x00, 0x03, len(self.address_digest), str(self.address_digest), 0, 0)
      self.file_socket.write(relay_request)
      self.file_socket.flush()
      log.debug("Sent socks auth methods")

    def _send_socks_auth_methods_request(self):
      socks_authentication_methods = struct.pack(str('!BBB'), 0x05, 0x01, 0x00)
      self.file_socket.write(socks_authentication_methods)
      self.file_socket.flush()

    def _read_socks_auth_methods_response(self):
      header = self.file_socket.read(2)
      log.debug("Received socks authentication negotiation")
      unpacked = struct.unpack(str('!BB'), header)

      if unpacked[0] != 0x05 or unpacked[1] != 0x00:
        log.error("Socks negotiation failed")
        raise RuntimeError("Socks authentication negotiation failed")
      
    def _read_socks_relay_response(self):
      header = struct.unpack(str('!BBBBB'), self.file_socket.read(5))
      digest_length = header[-1]

      self.file_socket.read(digest_length)
      self.file_socket.read(2) # 2 more for port
        
'''
stanza objects
'''
class StreamHost(ElementBase):

  namespace = xep_0065.XMLNS
  name = 'streamhost'
  interfaces = set(('host', 'port', 'jid'))

class StreamHosts(ElementBase):
  plugin_attrib = 'streamhosts'
  namespace = xep_0065.XMLNS
  name = 'query'
  interfaces = set(('hosts','sid'))
  sub_interfaces = set(('hosts',))
  subitem = (StreamHost,)

  def get_hosts(self):
    streamhosts = {}
    for streamhost in self.xml.findall('{%s}streamhost' % StreamHost.namespace):
      s = StreamHost(streamhost)
      streamhosts[s['jid']] = s

    return streamhosts

  def set_hosts(self, streamhosts):
    for s in streamhosts:
      self.add_streamhost(s)

  def add_streamhost(self, streamhost):
    ''' 
    streamhost is dict: {"host" : ..., "port", ....}
    '''
    s = StreamHost(None, self)
    s['host'] = streamhost['host']
    s['port'] = streamhost['port']

class StreamHostUsed(ElementBase):
  namespace = xep_0065.XMLNS
  plugin_attrib = 'streamhost-used'
  name = 'streamhost-used'
  interfaces = set(('jid',))
