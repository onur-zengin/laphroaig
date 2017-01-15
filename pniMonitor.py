#!/usr/bin/env python

import sys
import getopt
import socket
import threading
import logging
import time
import subprocess
import re
import resource
import os
from datetime import datetime as dt


def tstamp(format):
    if format == 'hr':
        return time.asctime()
    elif format == 'mr':
        return dt.now()

oidlist = ['.1.3.6.1.2.1.31.1.1.1.1',  # IF-MIB::ifName
           '.1.3.6.1.2.1.31.1.1.1.18', # IF-MIB::ifDescr
           '.1.3.6.1.2.1.4.34.1.3',  # IP-MIB::ipAddressIfIndex
           '.1.3.6.1.4.1.9.9.187.1.2.5.1.6',  # cbgpPeer2LocalAddr
           '.1.3.6.1.4.1.9.9.187.1.2.5.1.11', # cbgpPeer2RemoteAs
           ".1.3.6.1.2.1.2.2.1.7",  # ifAdminStatus 1up 2down 3testing
           ".1.3.6.1.2.1.2.2.1.8",  # ifOperStatus 1up 2down 3testing 4unknown ...
           ".1.3.6.1.2.1.31.1.1.1.15",  # ifHighSpeed
           ".1.3.6.1.2.1.31.1.1.1.6",  # ifHCInOctets
           ".1.3.6.1.2.1.31.1.1.1.10",  # ifHCOutOctets
           ".1.3.6.1.4.1.9.9.187.1.2.5.1.3.1.4.2.120.9.120"
           ]

class Router(threading.Thread):
    dsc_oids = oidlist[:4]
    int_oids = oidlist[5:10]
    bw_oids = oidlist[7:10]
    bgp_oids = oidlist[10:]
    def __init__(self, threadID, node, dswitch, risk_factor):
        threading.Thread.__init__(self, name='thread-%d_%s' % (threadID, node))
        self.node = node
        self.switch = dswitch
        self.risk_factor = risk_factor
    def run(self):
        logging.debug("Starting")
        self.tstamp = tstamp('mr')
        self.ipaddr = self.dns(self.node)
        if self.switch is True:
            logging.info("New inventory file / inventory updates detected. Initializing node discovery")
            for f in os.listdir('.'):
                if self.node+'.dsc' in f or self.node+'.prb' in f:
                    os.remove(f)
            disc = self.discovery(self.ipaddr)
        else:
            try:
                with open('.do_not_modify_'.upper() + self.node + '.dsc') as tf:
                    disc = eval(tf.read())
            except IOError:
                logging.info("Discovery file(s) could not be located. Initializing node discovery")
                disc = self.discovery(self.ipaddr)
        print disc
        self.pni_interfaces = [int for int in disc if disc[int]['type'] == 'pni']
        self.cdn_interfaces = [int for int in disc if disc[int]['type'] == 'cdn']
        self.interfaces = self.pni_interfaces + self.cdn_interfaces
        self.process(self.ipaddr, disc)
        logging.debug("Completed")
    def dns(self,node):
        try:
            ipaddr = socket.gethostbyname(node)
        except socket.gaierror as gaierr:
            logging.warning("Operation halted: %s" % (str(gaierr)))
            sys.exit(3)
        except:
            logging.warning("Unexpected error while resolving hostname")
            logging.debug("Unexpected error while resolving hostname: %s" % (str(sys.exc_info()[:2])))
            sys.exit(3)
        return ipaddr
    def ping(self,ipaddr):
        try:
            ptup = subprocess.Popen(['ping', '-i', '0.2', '-w', '2', '-c', '500', ipaddr, '-q'], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE).communicate()
        except:
            logging.warning("Unexpected error during ping test")
            logging.debug("Unexpected error - Popen function ping(): %s" % (str(sys.exc_info()[:2])))
            sys.exit(3)
        else:
            if ptup[1] == '':
                n = re.search(r'(\d+)\%\spacket loss', ptup[0])
                if n is not None:
                    if int(n.group(1)) == 0:
                        pingr = 0
                    elif 0 < int(n.group(1)) < 100:
                        logging.warning("Operation halted. Packet loss detected")
                        sys.exit(3)
                    elif int(n.group(1)) == 100:
                        logging.warning("Operation halted. Node unreachable")
                        sys.exit(3)
                    else:
                        logging.warning("Unexpected error during ping test")
                        logging.debug("Unexpected regex error during ping test: ### %s ###" % (str(n)))
                        sys.exit(3)
                else:
                    logging.warning("Unexpected error during ping test")
                    logging.debug("Unexpected regex error during ping test: ### %s ###" % (str(ptup[0])))
                    sys.exit(3)
            else:
                logging.warning("Unexpected error during ping test")
                logging.debug("Unexpected error during ping test: ### %s ###" % (str(ptup)))
                sys.exit(3)
        return pingr
    def discovery(self, ipaddr):
        pni_interfaces = []
        cdn_interfaces = []
        disc = {}
        ifNameTable, ifDescrTable, ipTable, peerTable = tuple([i.split(' ') for i in n] for n in
                                            map(lambda oid: self.snmp(ipaddr, [oid], quiet='off'), self.dsc_oids))
        for i, j in zip(ifDescrTable, ifNameTable):
            if 'no-mon' not in i[3] and '[CDPautomation:PNI]' in i[3]:
                pni_interfaces.append(j[3])
                disc[j[3]] = {'ifIndex': j[0].split('.')[1]}
                disc[j[3]]['type'] = 'cdn'
            elif 'no-mon' not in i[3] and '[CDPautomation:CDN]' in i[3]:
                cdn_interfaces.append(j[3])
                disc[j[3]] = {'ifIndex': j[0].split('.')[1]}
                disc[j[3]]['type'] = 'pni'
        for interface in pni_interfaces:
            for i in ipTable:
                if disc[interface]['ifIndex'] == i[3]:
                    type = i[0].split('"')[0].split('.')[1]
                    if type == 'ipv4' or type == 'ipv6':
                        if not disc[interface].has_key('local_' + type):
                            disc[interface]['local_' + type] = [i[0].split('"')[1]]
                        else:
                            disc[interface]['local_' + type] += [i[0].split('"')[1]]
        for interface in pni_interfaces:
            for i in peerTable:
                if len(i) == 8:
                    locaddr = ('.').join([str(int(i[n], 16)) for n in range(3, 7)])
                    if disc[interface].has_key('local_ipv4'):
                        if locaddr in disc[interface]['local_ipv4']:
                            peeraddr = ('.').join(i[0].split('.')[-4:])
                            if not disc[interface].has_key('peer_ipv4'):
                                disc[interface]['peer_ipv4'] = [peeraddr]
                            else:
                                disc[interface]['peer_ipv4'] += [peeraddr]
                elif len(i) == 20:
                    locaddr = (':').join([str(i[n]) for n in range(3, 19)])
                    if disc[interface].has_key('local_ipv6'):
                        if locaddr in disc[interface]['local_ipv6']:
                            peeraddr = (':').join([format(int(n), '02x') for n in i[0].split('.')[-16:]])
                            if not disc[interface].has_key('peer_ipv6'):
                                disc[interface]['peer_ipv6'] = [peeraddr]
                            else:
                                disc[interface]['peer_ipv6'] += [peeraddr]
        with open('.do_not_modify_'.upper()+self.node+'.dsc', 'w') as tf:
            tf.write(str(disc))
        return disc
    def probe(self, ipaddr, disc):
        old = []
        new = []
        args = ['tail', '-1', '.do_not_modify_'.upper() + ipaddr + '.prb']
        try:
            ptup = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        except:
            logging.warning("Unexpected error during probe operation")
            logging.debug("Unexpected error - Popen function probe(): %s" % (str(sys.exc_info()[:2])))
            sys.exit(3)
        else:
            if ptup[1] == '':
                old = eval(ptup[0])
            elif "No such file or directory" in ptup[1]:
                logging.info("New Node")
            else:
                logging.warning("Unexpected error during %s operation" % (str(ptup)))
                logging.debug("Unexpected error during %s operation: ### %s ###" % (str(ptup)))
                sys.exit(3)
        finally:
            for interface in disc:
                int_new = self.snmp(ipaddr, [i + '.' + disc[interface]['ifIndex'] for i in self.int_oids],
                                    cmd='snmpget')
                int_new.insert(0, str(self.tstamp))
                int_new.insert(0, interface)
                new.append(int_new)
            with open('.do_not_modify_'.upper() + self.node + '.prb', 'a') as pf:
                pf.write(str(new)+'\n')
        return old, new
    def snmp(self, ipaddr, oids, cmd='snmpwalk', quiet='on'):
        args = [cmd, '-v2c', '-c', 'kN8qpTxH', ipaddr]
        if quiet is 'on':
            args.insert(1, '-Oqv')
        args += oids
        try:
            stup = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        except:
            logging.warning("Unexpected error during %s operation" % (cmd))
            logging.debug("Unexpected error - Popen function snmp(): %s" % (str(sys.exc_info()[:2])))
            sys.exit(3)
        else:
            if stup[1] == '':
                snmpr = stup[0].strip('\n').split('\n')
                # elif timeout self.ping(self.ipaddr)
            else:
                logging.warning("Unexpected error during %s operation" % (cmd))
                logging.debug("Unexpected error during %s operation: ### %s ###" % (cmd, str(stup)))
                sys.exit(3)
        return snmpr
    def process(self, ipaddr, disc):
        old, new = self.probe(ipaddr, disc)
        actCdnIn, aggCdnIn, actPniOut, aggPniOut, dateFormat = 0, 0, 0, 0, "%Y-%m-%d %H:%M:%S.%f"
        if old is not '':
            for o , n in zip(old, new):
                if n[0] in self.cdn_interfaces:
                    if o[3] == 'up' and n[3] == 'up':
                        delta_time = (dt.strptime(n[1], dateFormat) - dt.strptime(o[1], dateFormat)).total_seconds()
                        delta_inOct = int(n[5]) - int(o[5])
                        int_util = (delta_inOct * 800) / (delta_time * int(n[4]) * 10**6)
                        disc[n[0]]['util'] = int_util
                        actCdnIn += int_util
                    if n[3] == 'up':
                        aggCdnIn += int(n[4])
                elif n[0] in self.pni_interfaces:
                    if o[3] == 'up' and n[3] == 'up':
                        delta_time = (dt.strptime(n[1], dateFormat) - dt.strptime(o[1], dateFormat)).total_seconds()
                        delta_outOct = int(n[6]) - int(o[6])
                        print n[0], "octets" , delta_outOct , "time" , delta_time
                        int_util = (delta_outOct * 800) / (delta_time * int(n[4]) * 10**6)
                        actPniOut += int_util
                    if n[3] == 'up':
                        aggPniOut += int(n[4])
            print "Active CDN Capacity: %.2f" % aggCdnIn
            print "Actual CDN Ingress: %.2f" % actCdnIn
            print "Usable PNI Capacity: %.2f" % aggPniOut
            print "Actual PNI Egress: %.2f" % actPniOut
            print [util for util in [disc[interface]['util'] for interface in self.cdn_interfaces]]
            print min([util for util in [disc[interface]['util'] for interface in self.cdn_interfaces]])
        else:
            pass # make sure the following lines don't fail due to unknown argument
        #if actPniOut / aggPniOut * 100 >= self.risk_factor: # consider nesting the following if to the one above
         #   self.acl('block', min([util for util in [disc[interface]['util'] for interface in self.cdn_interfaces]]))
    def acl(self, decision, interface):
        if decision == 'block':
            logging.warning("%s will now be blocked" % (interface))
        else:
            logging.info("%s will now be unblocked" % (interface))



def parser(lst):
    dict = {}
    for node in [line.split(':') for line in lst]:
        dict[node[0]] = {}
        for i in range(len(node))[1:]:
            dict[node[0]][node[i].split(',')[0]] = [int for int in node[i].split(',')[1:]]
    return dict


def usage(args):
    print 'USAGE:\n\t%s\t[-i <filename>] [--input <filename>] [-l <loglevel>] [--logging <loglevel>]' \
          '\n\t\t\t[-f <value>] [--frequency <value>] [-r <value>] [--repeat <value>]' % (args[0])
    print '\nDESCRIPTION:\n\t[-i <filename>], [--input <filename>]' \
          '\n\t\tThe inventory details must be provided in a text file structured in the following format,' \
          'while each node being written on a separate line:' \
          '\n\t\t\t<nodename>:pni,<intname-1>,...,<intname-M>:cdn,<intname-1>,...,<intname-N>' \
          '\n\t\t\tEXAMPLE: er12.thlon:pni,Bundle-Ether1024,Bundle-Ether1040:cdn,Bundle-Ether1064' \
          '\n\t[-l <loglevel>], [--logging <loglevel>]' \
          '\n\t\tThe loglevel must be specified as one of INFO, WARNING, DEBUG in capital letters. ' \
          'If none specified, the program will run with default level INFO.'


def main(args):
    asctime = tstamp('hr')
    try:
        with open("pniMonitor.conf") as pf:
            parameters = [tuple(i.split('=')) for i in
                            filter(lambda line: line[0] != '#', [n.strip('\n') for n in pf.readlines()])]
    except IOError as ioerr:
        print ioerr
        sys.exit(1)
    else:
        try:
            for opt, arg in parameters:
                if opt == 'inputfile':
                    inputfile = arg
                elif opt == 'loglevel':
                    if arg.lower() in ('info', 'warning', 'debug'):
                        loglevel = arg.upper()
                    else:
                        print 'Invalid value specified for loglevel, program will continue with its default ' \
                              'setting: "info"'
                        loglevel = 'info'
                elif opt == 'risk_factor':
                    try:
                        risk_factor = int(arg)
                    except ValueError:
                        print 'The value of the risk_factor argument must be an integer'
                        sys.exit(2)
                    else:
                        if not 0 <= risk_factor and risk_factor <= 100:
                            print 'The value of the risk_factor argument must be an integer between 0 and 100'
                            sys.exit(2)
                elif opt == 'frequency':
                    try:
                        frequency = int(arg)
                    except ValueError:
                        print 'The value of the frequency argument must be an integer'
                        sys.exit(2)
                elif opt == 'runtime':
                    if arg.lower() == 'infinite':
                        runtime = 'infinite'
                    else:
                        try:
                            runtime = int(arg)
                        except ValueError:
                            print 'The value of the runtime argument must be either be "infinite" or an integer'
                            sys.exit(2)
                else:
                    print "Invalid parameter found in the configuration file: %s" % (opt)
                    sys.exit(2)
        except ValueError:
            print "Configuration parameters must be provided in key value pairs separated by an equal sign (=)" \
                  "\nExample:\n\tfrequency=5\n\tloglevel=info"
            sys.exit(2)
    try:
        options, remainder = getopt.getopt(args, "i:hl:r:f:", ["input=", "help", "logging=", "runtime=", "frequency="])
    except getopt.GetoptError as err:
        print err
        usage(sys.argv)
        sys.exit(2)
    for opt, arg in options:
        if opt in ('-h','--help'):
            usage(sys.argv)
            sys.exit(2)
        elif opt in ('-i', '--input'):
            inputfile = arg
        elif opt in ('-l','--logging'):
            if arg.lower() in ('INFO','WARNING','DEBUG'):
                loglevel = arg.upper()
            else:
                loglevel = 'INFO'
        elif opt in ('-r', '--runtime'):
            if arg.lower() == 'infinite':
                runtime = 'infinite'
            else:
                try:
                    runtime = int(arg)
                except ValueError:
                    print 'The value of the runtime argument must either be "infinite" or an integer'
                    sys.exit(2)
        elif opt in ('-f','--frequency'):
            try:
                frequency = int(arg)
            except ValueError:
                print 'The value of the frequency (-f) argument must be an integer'
                sys.exit(2)
        else:
            print "Unhandled option: %s" % (opt)
            sys.exit(2)
    logging.basicConfig(level=logging.getLevelName(loglevel),
                        format='%(asctime)-15s [%(levelname)s] %(threadName)-10s: %(message)s')  # FIXME
                                                                                            # revisit formatting %-Ns
    lastChanged = ""
    while True:
        try:
            with open(inputfile) as sf:
                inventory = filter(lambda line: line[0] != '#', [n.strip('\n') for n in sf.readlines()])
            if lastChanged != os.stat(inputfile).st_mtime:
                dswitch = True
            else:
                dswitch = False
        except IOError as ioerr:
            print ioerr
            sys.exit(1)
        except OSError as oserr:
            print oserr
            sys.exit(1)
        else:
            threads = []
            logging.debug("Initializing subThreads")
            for n,node in enumerate(inventory):
                t = Router(n+1, node, dswitch, risk_factor)
                threads.append(t)
                t.start()
            for t in threads:
                t.join()
            lastChanged = os.stat(inputfile).st_mtime
            if type(runtime) == int:
                runtime -= 1
        finally:
            if runtime == 0:
                break
            time.sleep(frequency)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        main(sys.argv[1:])
    else:
        usage(sys.argv)
        sys.exit(2)