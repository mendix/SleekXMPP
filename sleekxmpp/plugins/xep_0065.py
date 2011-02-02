"""
Currently under dev by Mendix.com
license to be announced, some type of opensource though
"""

from __future__ import division, with_statement, unicode_literals
import sys
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
    OPEN_STREAM_XPATH = MatchXPath('{%s}iq/{%s}open'  %(DEFAULT_NS, XMLNS))
    CLOSE_STREAM_XPATH = MatchXPath('{%s}iq/{%s}close' %(DEFAULT_NS, XMLNS))

       
    def plugin_init(self):
        self.xep = '0065'
        self.description = 'Socks5 Bytestreams'
        self.incoming_files = {}

        self.acceptTransfers = self.config.get('acceptTransfers', True)
        self.saveDirectory = self.config.get('saveDirectory', '/tmp/')
        self.maxSessions = self.config.get('maxSessions', 2)
        self.transferTimeout = self.config.get('transferTimeout', 120) #how long we should wait between data messages until we consider the stream invalid
        #thread setup
        self.streamSessions = {} #id:thread
        self.__streamSetupLock = threading.Lock()
        #Register the xmpp stanzas used in this plugin
        register_stanza_plugin(Iq, StreamHosts)
        register_stanza_plugin(Iq, StreamHostUsed)

        # create callbacks:
        socket_negotiation_callback = Callback('negotiate_file_transfer_socket', xep_0065.SOCKET_NEGOTIATE_XPATH, self._handle_socket_negotiation)
        open_stream_callback = Callback('xep_0065_open_stream', xep_0065.OPEN_STREAM_XPATH, self._handle_incoming_transfer_request, thread=True)
        close_stream_callback = Callback('xep_0065_close_stream', xep_0065.CLOSE_STREAM_XPATH, self._handle_stream_closed, thread=False)

        #add handlers to listen for incoming requests:
        self.xmpp.register_handler(close_stream_callback)
        self.xmpp.register_handler(open_stream_callback)
        self.xmpp.register_handler(socket_negotiation_callback)

        #Event handler to allow session threads to call back to the main processor to remove the thread
        self.xmpp.add_event_handler(STREAM_CLOSED_EVENT, self._event_close_stream, threaded=True, disposable=False)
        
    def post_init(self):
        xep_0096.FileTransferProtocol.post_init(self)
        if self.xmpp.plugin.get('xep_0030'):
            self.xmpp.plugin['xep_0030'].add_feature(xep_0065.XMLNS)
        
    def sendFile(self, fileName, to, threaded=True, sid=None):
        '''
        Sends a file to the intended receiver if the receiver is available and 
        willing to accept the transfer.  If the send is requested to be threaded 
        the session sid will be returned, otherwise the method will block until 
        the file has been sent and the session closed.
        
        The returned sid can be used to check on the status of the transfer or 
        cancel the transfer.
        
        Error Conditions:
        -IOError will be raised if the file to be sent is not found
        -TooManySessionsException will be raised if there are already more than 
        self.maxSessions running (configurable via plugin configuration)
        -Exception will be raised if the sender is not available
        -NotAcceptableException will be raised if the sender denies the transfer request
        or if the sender full JID is equal to the recipient 
        -InBandFailedException will be raised if there is an error during the
        file transfer
        '''
        pass
    
    def getSessionStatus(self, sid):
        '''
        Returns the status of the transfer specified by the sid.  If the session
        is not found none will be returned.
        '''
        session = self.streamSessions.get(sid)
        if session:
            return session.getStatus()
        else:
            return None
        
    def getSessionStatusAll(self):
        dict = {}
        for session in self.streamSessions.values():
            dict[session.sid] = session.getStatus()
        
        return dict
        
    def cancelSend(self, sid):
        '''
        cancels an outgoing file transfer.
        If the session is not found, method will pass
        '''
        session = self.streamSessions.get(sid)
        if session:
            session.cancelStream()
            
    def setAcceptStatus(self, status):
        '''
        sets if plugin will accept in-band file transfers or not.
        if switching from true to false any currently working sessions will 
        finish
        '''
        self.acceptTransfers = status

    def start_receive_file(self, file_name, file_length, sid):
      self.incoming_files[sid] = {"name" : file_name, "length" : file_length}
      log.debug("A file was announced to me: %s " % file_name)

    def _handle_incoming_transfer_request(self, iq):
      print("handling incoming request")
        

    def _handle_stream_closed(self, iq):
      print("handle stream closed")
        

    def _event_close_stream(self, iq):
      print("event close stream")
        
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
        print("Available streamhost: %s:%s" % (s['host'], s['port']))
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
      log.debug("send socks auth methods")

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

      digest = self.file_socket.read(digest_length)
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

'''
Override of the threading.Event class to make the implementation work like 
python 2.7
'''
def Event(*args, **kwargs):
    if sys.version_info < (2,7):
        return _Event(*args, **kwargs)
    else:
        return threading.Event(*args, **kwargs)

class _Event(object):

    #Modification of Event class from python 2.6 because the 2.7 version is better

    def __init__(self):
        self.__cond = threading.Condition(threading.Lock())
        self.__flag = False

    def isSet(self):
        return self.__flag

    is_set = isSet

    def set(self):
        self.__cond.acquire()
        try:
            self.__flag = True
            self.__cond.notify_all()
        finally:
            self.__cond.release()

    def clear(self):
        self.__cond.acquire()
        try:
            self.__flag = False
        finally:
            self.__cond.release()

    def wait(self, timeout=None):
        self.__cond.acquire()
        try:
            if not self.__flag:
                self.__cond.wait(timeout)
            return self.__flag
        finally:
            self.__cond.release()
