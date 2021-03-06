#
#   dummyteletype --  fake Baudot Teletype emulator
#
#   Talks to computer console window.  For debug usage.
#   Only activated when configured port is "TEST"
#
#   License: LGPL.
#
#   John Nagle
#   June, 2015
#
import logging
import baudot
import threading
import sys
import os
#
#   Constants
#
#   ASCII
ESC = 0x1b                                          # ESC char, ASCII/UNICODE
CTLC = 0x03                                         # control-C

#   Baudot
BLANKKEY = 0x00                                     # the all ones char in Baudot     

class Getch:
    """
    Gets a single character from standard input.  
    Does not echo to the screen.
    Does not affect output console processing.
    """
    def __init__(self):
        try:
            self.impl = _GetchWindows()
        except ImportError:
            self.impl = _GetchUnix()

    def __call__(self): return self.impl()


class _GetchUnix:
    def __init__(self):
        import tty, sys

    def __call__(self):
        import sys, tty, termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            settings = old_settings                     # save old settings
            settings[3] = settings[3] & ~termios.ICANON # turn off "canonical processing" of input
            termios.tcsetattr(fd, termios.TCSADRAIN, settings)
            ch = sys.stdin.read(1)                      # read one char
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch


class _GetchWindows:
    def __init__(self):
        import msvcrt

    def __call__(self):
        import msvcrt
        ch = msvcrt.getch()
        ch0 = ch[0]
        if isinstance(ch0, int) :                   # if Python 3.x
            return(chr(ch0))
        return(ch0)                                 # Python 2.x
       
#
#   Dummyteletype  -- dummy serial object
#
class Dummyteletype(object) :                       # really should inherit from raw I/O device

    def __init__(self, port, timeout, baudrate) :
        """
        Constructor
        """
        self.baudot = baudot.Baudot("USTTY")        # our dummy Teletype speaks USTTY, not ITA2
        self.port = port                            # name of port, for messages
        self.timeout = timeout                      # set port properties
        self.baudrate = baudrate
        self.dtr = False
        self.rts = False
        logging.basicConfig()                       # configure logging system
        self.logger = logging.getLogger(port)       # main logger for this port
        self.logger.setLevel(logging.INFO)          # always log if using dummy teletype
        self.inshift = None                         # input side shift state
        self.outshift = None                        # output side shift state
        self.outline = ''                           # line to output
        self.flushtimer = None                      # no timer yet
        self.flushlock = threading.Lock()           # flushing lock
        self.getch = Getch()                        # portable get character        
       
    def flushOutput(self) :                         # flush queued output
        with self.flushlock :                       # critical section
            if len(self.outline) > 0 :              # if anything queued
                self.logger.info(self.outline)      # log it
                self.outline = ''                   # clear line
        
    def write(self, s) :                                  
        """
        Write to dummy teletype. Input is Baudot as type bytes
        """
        for bb in s :                               # for Baudot bytes
            if bb == baudot.Baudot.FIGS :           # if FIGS shift
                self.outshift = bb
                continue
            if bb == baudot.Baudot.LTRS :           # if LTRS shift
                self.outshift = bb
                continue
            ch = self.baudot.chToASCII(bb, self.outshift) # convert to ASCII            
            ####self.logger.info("Output as Baudot: %s <- %d (shift %s)" % (ch, bb, self.outshift))  # ***TEMP***
            if not ch is None :                 
                if ch in ['\n','\r'] :
                    self.flushOutput()              # display if anything available
                else :
                    with self.flushlock:            # critical section
                        self.outline = self.outline + ch # add to output line
        self.inshift = self.outshift                # keep both sides in sync
        #   If no output has been sent for 0.5 secs, flush the output buffer.
        #   This gets us full lines of output.
        if self.flushtimer is not None :
            self.flushtimer.cancel()                # cancel old timer
        self.flushtimer = threading.Timer(0.5, self.flushOutput) # inefficient, but debug only
        self.flushtimer.daemon = True               # make it a daemon
        self.flushtimer.start()                     # start timer again
            
        
    def read(self) :
        """
        Read from dummy Teletype.  Returns Baudot as type bytes. Blocking.
        
        Assumes ASCII keyboard.
        
        ESC sends a break. 
        
        Anything other than a single char has a CR appended.     
        """
        intext = self.getch()                       # Console ASCII input from simulated keyboard
        self.flushOutput()                          # flush any pending output
        baudotread = bytearray()                    # assemble bytes
        intexta = intext.encode("ASCII","replace")  # convert to bytes
        for inbyte in intexta :                     # for all input chars
            if isinstance(inbyte, int) :            # Python 2/3 problem
                inbyte = chr(inbyte)                # make it char
            ####self.logger.info("Keyboard char: %s" % (repr(inbyte),)) # ***TEMP***
            if inbyte == chr(CTLC) :
                self.logger.info("Control-C abort")
                os._exit(0)                         # this is the normal exit
            if inbyte == "\n" :
                inbyte = '\r'                       # newline is a CR in Teletype land
            #   Special cases
            if inbyte == chr(ESC): # Python 2/3 silliness
                self.logger.info("Keyboard simulated BREAK")
                (bb, shift) = (0,None)              # If ESC char, simulate BREAK
            elif inbyte == '\b' or inbyte == chr(0x7f) : # Backspace, which is 0x7f on Linux
                (bb, shift) = (BLANKKEY, None)      # if backspace, simulate blank key
            else :
                (bb, shift) = self.baudot.chToBaudot(inbyte)  # convert ASCII char to Baudot
            if (not (shift is None)) and shift != self.inshift : # if shifting implied
                baudotread.append(shift)            # put LTRS or FIGS in output
                self.inshift = shift
            if bb is None :
                self.logger.error("Cannot type %s on a Baudot keyboard." % (repr(inbyte),))
                continue
            baudotread.append(bb)                   # add baudot char
        if len(baudotread) != 1 :                   # if not a single char, 
            baudotread.append(baudot.Baudot.CR)     # add an ending CR in Baudot
        return(baudotread)                            
        
    def close() :
        pass                                        # nothing to do
        
#
#   Serial -- create a dummy serial object connected to a dummy teletype
#
def Serial(port, baudrate, timeout, bytesize, parity, stopbits) :
    """
    Create a dummy serial object
    """
    return(Dummyteletype(port, timeout, baudrate))      # return a dummy serial object
          

