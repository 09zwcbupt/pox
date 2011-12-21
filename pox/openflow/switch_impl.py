"""
Software OpenFlow Switch

@author: Colin Scott (cs@cs.berkeley.edu)

Based heavily on pylibopenflow:

Copyright(C) 2009, Stanford University
Date November 2009
Created by ykk
"""

# TODO: Don't have SwitchImpl take a socket object... Should really have a 
# OF_01 like task that listens for socket connections, creates a new socket, 
# wraps it in a ControllerConnection object, and calls SwitchImpl._handle_ConnectionUp

from pox.core import core
from pox.openflow.libopenflow_01 import *
from pox.openflow.of_01 import make_type_to_class_table, deferredSender

from errno import EAGAIN
from collections import namedtuple

log = core.getLogger()

class SwitchImpl(object):
  # ports is a list of ofp_phy_ports
  def __init__(self, dpid, sock, name=None, ports=[], miss_send_len=128,
               n_buffers=100, n_tables=1, capabilities=None):
    """Initialize switch"""
    ##Datapath id of switch
    self.dpid = dpid
    ## Human-readable name of the switch
    self.name = name
    if self.name is None:
      self.name = str(dpid) 
    ##Number of buffers
    self.n_buffers = n_buffers
    ##Number of tables
    self.n_tables= n_tables
    # TODO: don't assume a single table
    self.table = FlowTable()
    ## Hash of port_no -> openflow.pylibopenflow_01.ofp_phy_ports
    self.ports = {}
    for port in ports:
      self.ports[port.port_no] = port
    ## (OpenFlow Handler map)
    ofp_handlers = {
       # Reactive handlers
       ofp_type_rev_map['OFPT_HELLO'] : self._receive_hello,
       ofp_type_rev_map['OFPT_ECHO_REQUEST'] : self._receive_echo,
       ofp_type_rev_map['OFPT_FEATURES_REQUEST'] : self._receive_features_request,
       ofp_type_rev_map['OFPT_FLOW_MOD'] : self._receive_flow_mod,
       ofp_type_rev_map['OFPT_PACKET_OUT'] : self._receive_packet_out,
       ofp_type_rev_map['OFPT_BARRIER_REQUEST'] : self._receive_barrier_request,
       
       # Proactive responses
       ofp_type_rev_map['OFPT_ECHO_REPLY'] : self._receive_echo_reply
       # TODO: many more packet types to process
    }
    ##Reference to connection with controller
    self._connection = ControllerConnection(sock, ofp_handlers)
    ##Capabilities
    if (isinstance(capabilities, SwitchCapabilities)):
        self.capabilities = capabilities
    else:
        self.capabilities = SwitchCapabilities(miss_send_len)

  def demux_openflow(self, raw_bytes):
    pass
    
  # ==================================== #
  #    Reactive OFP processing           #
  # ==================================== #
  def _receive_hello(self, packet):
    log.debug("Receive hello %s" % self.name)
    # How does the OpenFlow protocol prevent an infinite loop of Hello messages?
    self.send_hello() 

  def _receive_echo(self, packet):
    """Reply to echo request
    """
    log.debug("Reply echo of xid: %s %s" % (str(packet), self.name)) # TODO: packet.xid
    msg = ofp_echo_request()
    self._connection.send(msg)
    
  def _receive_features_request(self, packet):
    """Reply to feature request
    """
    log.debug("Reply features request of xid %s %s" % (str(packet), self.name)) # TODO: packet.xid
    msg = ofp_features_reply(datapath_id = self.dpid, n_buffers = self.n_buffers, 
                             n_tables = self.n_tables,
                             capabilities = self.capabilities.get_capabilities(),
                             actions = self.capabilities.get_actions(),
                             ports = self.ports.values())
    self._connection.send(msg)
                                
  def _receive_flow_mod(self, packet):
    """Handle flow mod: just print it here
    """
    log.debug("Flow mod %s: %s" % (self.name, packet.show()))
    self.table.process_flow_mod(packet)
    
  def _receive_packet_out(self, packet):
    """
    Send the packet out the given port
    """
    # TODO: There is a packet formatting error somewhere... str(packet) throws
    # an exception... no method "show" for None
    log.debug("Packet out") # , str(packet)) 
    
  def _receive_echo_reply(self, packet):
    log.debug("Echo reply: %s %s" % (str(packet), self.name))
    
  def _receive_barrier_request(self, packet):
    log.debug("Barrier request %s %s" % (self.name, str(packet)))
    msg = ofp_barrier_reply(xid = packet.xid)
    self._connection.send(msg)
    
  # ==================================== #
  #    Proactive OFP processing          #
  # ==================================== #
  def send_hello(self):
    """Send hello
    """
    log.debug("Send hello %s " % self.name)
    msg = ofp_hello()
    self._connection.send(msg)

  def send_packet_in(self, inport, bufferid=None, packet="", xid=0, reason=None):
    """Send PacketIn
    Assume no match as reason, bufferid = 0xFFFFFFFF,
    and empty packet by default
    """
    log.debug("Send PacketIn %s " % self.name)
    if (reason == None):
      reason = ofp_packet_in_reason_rev_map['OFPR_NO_MATCH']
    if (bufferid == None):
      bufferid = int("0xFFFFFFFF",16)
    
    msg = ofp_packet_in(inport = inport, bufferid = bufferid, reason = reason, 
                        data = packet)
    self._connection.send(msg)
    
  def send_echo(self, xid=0):
    """Send echo request
    """
    log.debug("Send echo %s" % self.name)
    msg = ofp_echo_request()
    self._connection.send(msg)
        
class ControllerConnection (object):
  # Unlike of_01.Connection, this is persistent (at least until we implement a proper
  # recoco Connection Listener loop)
  # Globally unique identifier for the Connection instance
  ID = 0

  def msg (self, m):
    #print str(self), m
    log.debug(str(self) + " " + str(m))
  def err (self, m):
    #print str(self), m
    log.error(str(self) + " " + str(m))
  def info (self, m):
    pass
    #print str(self), m
    log.info(str(self) + " " + str(m))

  def __init__ (self, sock, ofp_handlers):
    self.sock = sock
    self.buf = ''
    ControllerConnection.ID += 1
    self.ID = ControllerConnection.ID
    ## OpenFlow Message map
    self.ofp_msgs = make_type_to_class_table()
    ## Hash from ofp_type -> handler(packet)
    self.ofp_handlers = ofp_handlers
    
  def fileno (self):
    return self.sock.fileno()

  def send (self, data):
    """
    Send raw data to the controller.

    Generally, data is a bytes object. If not, we check if it has a pack()
    method and call it (hoping the result will be a bytes object).  This
    way, you can just pass one of the OpenFlow objects from the OpenFlow
    library to it and get the expected result, for example.
    """
    # TODO: this is taken directly from of_01.Connection. Refoactor to reduce
    # redundancy
    if type(data) is not bytes:
      if hasattr(data, 'pack'):
        data = data.pack()

    if deferredSender.sending:
      log.debug("deferred sender is sending!")
      deferredSender.send(self, data)
      return
    try:
      l = self.sock.send(data)
      if l != len(data):
        self.msg("Didn't send complete buffer.")
        data = data[l:]
        deferredSender.send(self, data)
    except socket.error as (errno, strerror):
      if errno == EAGAIN:
        self.msg("Out of send buffer space.  Consider increasing SO_SNDBUF.")
        deferredSender.send(self, data)
      else:
        self.msg("Socket error: " + strerror)
        self.disconnect()

  def read (self):
    """
    Read data from this connection.

    Note: if no data is available to read, this method will block. Only invoke
    after select() has returned this socket.
    """
    # TODO: this is taken directly from of_01.Connection. The only difference is the 
    # event handlers. Refactor to reduce redundancy.
    d = self.sock.recv(2048)
    if len(d) == 0:
      return False
    self.buf += d
    l = len(self.buf)
    while l > 4:
      if ord(self.buf[0]) != OFP_VERSION:
        log.warning("Bad OpenFlow version (" + str(ord(self.buf[0])) +
                    ") on connection " + str(self))
        return False
      # OpenFlow parsing occurs here:
      ofp_type = ord(self.buf[1])
      packet_length = ord(self.buf[2]) << 8 | ord(self.buf[3])
      if packet_length > l: break
      msg = self.ofp_msgs[ofp_type]()
      msg.unpack(self.buf)
      self.buf = self.buf[packet_length:]
      l = len(self.buf)
      try:
        if ofp_type not in self.ofp_handlers:
          raise RuntimeError("No handler for ofp_type %d" % ofp_type)
       
        h = self.ofp_handlers[ofp_type]
        h(msg)
      except Exception as e:
        log.exception(e)
        #log.exception("%s: Exception while handling OpenFlow message:\n%s %s",
        #              self,self,("\n" + str(self) + " ").join(str(msg).split('\n')))
        continue
    return True
  
  def __str__ (self):
    return "[Con " + str(self.ID) + "]"
        
        
# FlowTable Entries (immutable):
#   match - ofp_match (13-tuple)
#   counters - hash from name -> count. May be stale
#   actions - ordered list of ofp_action_*s to apply for matching packets
class TableEntry (namedtuple('TableEntry', 'match counters actions')):
  @staticmethod
  def from_flow_mod(flow_mod):
    match = flow_mod.match
    counters = {
      "idle_timout" : flow_mod.idle_timeout,
      "hard_timout" : flow_mod.hard_timeout,
      # TODO: more counters!
    }
    
    actions = flow_mod.actions
    # TODO: More metadata? e.g., out_port, priority, flags
    return TableEntry(match, counters, actions)
    
class FlowTable (object):
  """
  Field of OpenFlowSwitch representing the flow table on the physical switch
  """
  def __init__(self):
    # For now we represent the table as a multidimensional array.
    #
    # [ (match, counters, actions),
    #   (match, counters, actions),
    #    ...                        ]
    # 
    # Implies O(N) lookup for now. TODO: fix
    self._table = []
    
  def process_flow_mod(self, flow_mod):
    if flow_mod.command == OFPFC_ADD:
      entry = TableEntry.from_flow_mod(flow_mod) 
      self.addEntry(entry)
    
    # TODO: implement section 4.6 of OpenFlow 1.0 specification:
    #  elif flow_mod.command == OFPC_DELETE, etc.
    #       alternatively, define a handler hash
    
  def addEntry(self, entry):
    if not isinstance(entry, TableEntry):
      raise "Not an Entry type"
   
    matching_entries = self.matching_entries(entry)
    if matching_entries:
      pass # TODO: do something
     
    self._table.append(entry)
  
  def matching_entries(self, entry):
    return [] # TODO: implement me

class SwitchCapabilities:
    """
    Class to hold switch capabilities
    """
    def __init__(self, miss_send_len=128):
        """Initialize

        Copyright(C) 2009, Stanford University
        Date October 2009
        Created by ykk
        """
        ##Capabilities support by datapath
        self.flow_stats = True
        self.table_stats = True
        self.port_stats = True
        self.stp = True
        self.multi_phy_tx = False
        self.ip_resam = False
        ##Switch config
        self.send_exp = None
        self.ip_frag = 0
        self.miss_send_len = miss_send_len
        ##Valid actions
        self.act_output = True
        self.act_set_vlan_vid = True
        self.act_set_vlan_pcp = True
        self.act_strip_vlan = True
        self.act_set_dl_src = True
        self.act_set_dl_dst = True
        self.act_set_nw_src = True
        self.act_set_nw_dst = True
        self.act_set_tp_src = True
        self.act_set_tp_dst = True
        self.act_vendor = False

    def get_capabilities(self):
        """Return value for uint32_t capability field
        """
        value = 0
        if (self.flow_stats):
            value += ofp_capabilities_rev_map['OFPC_FLOW_STATS']
        if (self.table_stats):
            value += ofp_capabilities_rev_map['OFPC_TABLE_STATS']
        if (self.port_stats):
            value += ofp_capabilities_rev_map['OFPC_PORT_STATS']
        if (self.stp):
            value += ofp_capabilities_rev_map['OFPC_STP']
        if (self.multi_phy_tx):
            value += ofp_capabilities_rev_map['OFPC_MULTI_PHY_TX']
        if (self.ip_resam):
            value += ofp_capabilities_rev_map['OFPC_IP_REASM']
        return value

    def get_actions(self):
        """Return value for uint32_t action field
        """
        value = 0
        if (self.act_output):
            value += (1 << (ofp_action_type_rev_map['OFPAT_OUTPUT']+1))
        if (self.act_set_vlan_vid):
            value += (1 << (ofp_action_type_rev_map['OFPAT_SET_VLAN_VID']+1))
        if (self.act_set_vlan_pcp):
            value += (1 << (ofp_action_type_rev_map['OFPAT_SET_VLAN_PCP']+1))
        if (self.act_strip_vlan):
            value += (1 << (ofp_action_type_rev_map['OFPAT_STRIP_VLAN']+1))
        if (self.act_set_dl_src):
            value += (1 << (ofp_action_type_rev_map['OFPAT_SET_DL_SRC']+1))
        if (self.act_set_dl_dst):
            value += (1 << (ofp_action_type_rev_map['OFPAT_SET_DL_DST']+1))
        if (self.act_set_nw_src):
            value += (1 << (ofp_action_type_rev_map['OFPAT_SET_NW_SRC']+1))
        if (self.act_set_nw_dst):
            value += (1 << (ofp_action_type_rev_map['OFPAT_SET_NW_DST']+1))
        if (self.act_set_tp_src):
            value += (1 << (ofp_action_type_rev_map['OFPAT_SET_TP_SRC']+1))
        if (self.act_set_tp_dst):
            value += (1 << (ofp_action_type_rev_map['OFPAT_SET_TP_DST']+1))
        return value
      
