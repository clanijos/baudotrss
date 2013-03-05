#
#   twiliofeed.py  -  fetch SMS messages from our private server
#
#   John Nagle
#   November, 2010
#
#   Polls Twilio storage of SMS messages
#
#   The data returned is pure text, not HTML.  This is intended for
#   applications where the output is a printing device displaying
#   news updates.
#
#   This polls our own custom server, not Twilio. Twilio sends SMS
#   messages to our server, which replies to them and stores them.
#   Here, we are polling our server.
#
#   License: LGPL
#
#
import sys
import time
import Queue
import logging
import datetime
import httplib
import urllib
import urllib2
import BeautifulSoup
import feedmanager
import msgutils
import threading
import base64
import re
import placenames
#
#   Constants
#   
SERVERPOLLURL = "http://www.aetherltd.com/cgi/ttypoll.cgi?" # URL for polling
TWILIOBASE = "https://api.twilio.com/2010-04-01/"            # Twilio base URL

def expandplaceabbrev(fields, fieldname, placetable) :
    """
    Expand country abbreviations and US state abbreviations to full length
    """
    if fieldname in fields and fields[fieldname] in placetable :
        fields[fieldname] = placetable[fields[fieldname]]
        
def formatphonenumber(s) :
    """
    Format phone number for readability
    """
    if not s:
        return("(UNKNOWN)")                         # no phone
    if len(s) == 12 and s.startswith("+1") :        # if US phone number
       return("(" + s[2:5] + ") " + s[5:8] + "-" + s[8:])  # break apart US number
    if s.startswith("+") :                          # if ISO standard phone
       return("INTL " + s[1:])                      # international number
    return(s)                                       # otherwise unchanged


def maketimelocal(dt) :
    """
    Convert naive UTC datetime object to local datetime object, still naive.
    
    Conversion is based on whether DST is in effect now, not at the datetime time.  
    """
    if time.localtime().tm_isdst and time.daylight:
        offsetsecs = -time.altzone                  # get offset
    else:                                           # non-DST case
        offsetsecs = -time.timezone                 # get non-dst offset
    delta = datetime.timedelta(0, offsetsecs)       # construct timedelta
    return(dt + delta)                              # apply time delta


def doservercmd(logger, accountsid, ourphoneno, cmd, v1=None, v2=None) :
    """
    Send command to server at Aetheric (not Twilio), get XML reply
    """
    fields = {"accountsid" : accountsid, "phonenumber": ourphoneno, "cmd": cmd }
    if v1 :
        fields["v1"] = v1
    if v2:
        fields["v2"] = v2
    url = SERVERPOLLURL + urllib.urlencode(fields)      # construct cmd URL
    logger.debug("SMS server cmd: " + url)
    fd = urllib2.urlopen(url)                           # open url
    result = fd.read()                                  # read contents
    logger.debug("SMS server reply: " + unicode(result)[:40] + "...")
    fd.close()                                          # done with open
    return(result)                                      # return result
#
#    class SMSfeed  --  read SMS messages from a Google Voice account.
#
class Twiliofeed(feedmanager.Feed) :    

    kpollinterval = 15.0                                # poll this often (seconds)

    HTMLREWRITES = [                                    # rewrite rules for cleaning HTML data
        (re.compile(r'&mdash;'),'-'),                   # convert HTML escape for mdash
        (re.compile(r'&amp;'),'&'),                     # convert HTML escape for ampersand
        (re.compile(r'&\w+;'),'?'),                     # any other special chars become question mark
        (re.compile(r'&\#\w+;'),'?')                    # get numeric escapes, too.
        ]                                               # 

                    
    #
    #    Called from outside the thread
    #
    def __init__(self, accountsid, authtoken, ourphoneno, logger) :
        feedmanager.Feed.__init__(self, "SMS", logger)
        self.lock = threading.Lock()                    # lock object.  One login at a time.
        self.accountsid = accountsid                    # accounts ID - for all requests
        self.authtoken = authtoken                      # auth token
        self.ourphoneno = ourphoneno                    # our phone number
        self.errmsg = None                              # no pending error message
        self.hdrtitle = "Aetheric Message"
        self.url = self.hdrtitle
        self.msgfrom = None                             # phone number of last message returned to user
        self.logger = logger                            # debug og to here
        self.lastdelete = 0.0                           # time of last delete cycle
        self.lastserial = -1                            # last serial number read
        self.donequeue = Queue.Queue()                  # queue of items to be marked "Printed"

        
         
    def itemdone(self, item) :                          # item has been printed, mark as done
        #   Update permanent file of messages printed.
        #   This is updated only after the message has been printed, so we don't lose
        #   messages if the program crashes.
        #   Mark as printed on server
        serial = getattr(item,"serial",None)            # get serial
        if serial :                                     # if has serial
            self.donequeue.put(item)                    # network task will mark as done

    def getpollinterval(self) :                            # poll this often
        return(self.kpollinterval)

    def markallasread(self) :
        pass                                  # deliberately not supported for messages

    def unmarkallasread(self) :               # deliberately not supported for messages
        pass

    def formattext(self, msgitem) :                        # format a msg item, long form
        emsg = msgitem.errmsg
        #    Format for printing as display message
        if emsg :                                       # short format for errors
            s = "%s: %s\n" % (msgitem.msgtime, emsg)
            return(s)                                   # return with error msg
        #    Combine header, body and trailer
        items = [self.header, msgitem.formathdr("\n"), msgitem.body, self.trailer]
        items = [item for item in items if item is not None] # all non-null
        s = "\n".join(items) + "\n"                     # concat all non-null
        return(s)                                       # no error

    def summarytext(self, msgitem) :
        emsg = msgitem.errmsg
        #    Format for printing as display message
        if emsg :                                       # short format for errors
            s = "%s: %s\n" % (msgitem.msgtime, emsg)
            return(s)                                   # return with error msg
        fmt = "SMS %s -- %s"
        s = fmt % (msgitem.formathdr("  "), msgitem.body[:40])
        return(s)                                       # no error
     
    def twiliopost(self, url, fields) :                 # send to Twilio
        """
        Send POST request to Twilio server.
        """
        try :
            data = None                                 # no data yet
            if fields :                                 # if data to POST
                data = urllib.urlencode(fields)         # encode POST data
            fd = None                                   # no file handle yet
            #   Always send with basic authentication.
            req = urllib2.Request(url)                  # empty URL request
            authstring = base64.encodestring('%s:%s' % 
                (self.accountsid, self.authtoken))
            authstring = authstring.replace('\n', '')
            req.add_header("Authorization", "Basic %s" % authstring)
            fd = urllib2.urlopen(req, data, 20.0)       # do request
            s = fd.read()
            tree = BeautifulSoup.BeautifulStoneSoup(s)  # parse reply
            fd.close()                                  # done with fd
            return(None, tree)
        except IOError as message:                      # trouble          
            self.logger.error("Twilio error: " + str(message))
            status = getattr(message,"code", 599)       # get HTTP fail code
            return(status, None)                        # fails
        
    def sendSMS(self, number, text) :                   # sending capability
        """
        Send SMS message
        """
        try: 
            self.logger.info("Sending SMS to %s: %s" % (number, text))
            fields = {"From" : self.ourphoneno , 
                "To"  : number, "Body" : text }
            url = "%sAccounts/%s/SMS/Messages" % (TWILIOBASE, self.accountsid)
            (status, tree) = self.twiliopost(url, fields)    # send to Twilio
            if status :
                return(self.fetcherror("Problem No. %s sending message" %
                    (status,), None))
            if tree :                                   # if reply parsed
                tag = tree.find("status", recursive=True)
                if tag :
                    smsstatus = tag.string              # string in 
                    if smsstatus :
                        smsstatus = smsstatus.strip().lower()
                        if smsstatus == "queued" :
                            return(None)                # success
                        else :                          # fail
                            return(self.fetcherror(
                                'Problem "%s" sending message' % 
                                (smsstatus,), None))
            return(self.fetcherror(
                'Message sending service not responding properly',
                 None))
        except IOError as message:
            return(self.fetcherror("Input or output error", message))


    #    
    #    Called from within the thread
    #

    def gettitle(self) :                                # get feed title 
        return(self.hdrtitle)        
            
    def fetchitems(self) :                              # fetch more items from feed source
        try :
            #   Mark completed items as "printed" on the server.  This must be done before
            #   fetching, or we will print the same item twice.
            if not self.markitemsdone() :               # mark any outstanding done items as done
                return                                  # if fail, will retry
            #   With all "done" items marked on the server, if the feed is idle and nothing
            #   is printing, we get the next unprinted message starting at the beginning.
            #   This is done so that reprinting old messages is possible.  If a previously
            #   printed message is to be reprinted, it is marked "unprinted" on the server,
            #   and will be re-read after lastserial is set to -1.  This only happens when
            #   the feed goes idle, so reprinted messages are printed at a low priority.
            if self.isfeedidle() :                      # if feed is idle
                self.lastserial = -1                    # reset to start at beginning so reprint works
            while True :                                # until all available read
                self.logger.debug("Polling SMS server starting after serial #%s" 
                    % (self.lastserial,))
                replyxml = doservercmd(self.logger, self.accountsid, self.ourphoneno, "getnext", self.lastserial + 1, None) # get next msg
                newserial = self.handlereply(replyxml)  # handle message
                self.logger.debug("Poll complete.")
                if newserial and newserial > self.lastserial: # if got message
                    self.lastserial = newserial         # advance serial
                else :
                    break                               # otherwise done
        
        #    Exception handling
        except AttributeError as message :                # if trouble
            self.logerror(self.fetcherror("Internal error when fetching message", message))
        except IOError as message:
            self.logerror(self.fetcherror("Input or output error", message))

    #
    #   handlereply  -- input is XML of one or more newly received messages
    #
    def handlereply(self, replyxml) :
        msgitem = None                                  # accum message items here
        newserial = -1                                  # no serial number yet
        tree = None                                     # no tree yet
        try :
            #   Extract mesages from XML
            tree = BeautifulSoup.BeautifulStoneSoup(replyxml)  # parse XML into tree
            #   Find all message tags in reply. There should be one or zero.
            messagetags = tree.findAll("message", recursive=True)
            for messagetag in messagetags :             # for all received messages
                self.logger.debug("SMS msg as XML: \n" + messagetag.prettify())
                fields = {}                             # fields of msg
                for tag in messagetag.findAll(recursive=False) :  # tags at next lev
                    v = tag.string.strip()              # get value
                    if v != "" :                        # if nonempty
                        fields[tag.name.strip().lower()] = v # key, value
                #   Got message
                if not "serial" in fields :
                    raise EnvironmentError("Messaging server returned XML without a serial number")
                self.logger.debug("New SMS message, serial %s" % (fields["serial"],))
                newserial = max(newserial, int(fields["serial"]))  # advance serial
                self.handlemsg(fields)                  # handle the message
                
        except (EnvironmentError, AttributeError) as message :
            # Generate error message
            self.logger.error("SMS poll reply format error: %s" % (str(message),))
            if tree :
                self.logger.error("SMS poll reply was: %s" % (tree.prettify(),))
            fields = {"errormsg" : "1", "smsbody": str(message)}
            self.handlemsg(fields)
            return(newserial)                           # no new traffic
            
        return(newserial)                               # highest serial number seen

    def handlemsg(self, fields) :                       # handle message, queue
        errormsg = fields.get("errormsg","1")           # get error flag
        if errormsg and errormsg != "0":                # if error message
            self.logerror(fields.get("smsbody"))        # report error
        else :                                          # normal case
            self.processmsg(fields)                     # format and queue
            
    def processmsg(self, fields) :
        """
        Process message fields from XML for non-error message.  Formatting, mostly.
        """
        #   Get basic fields.  Empty fields are not present
        msgfrom = formatphonenumber(fields.get("smsfrom", "(SENDER UNKNOWN)"))
        rcvtime = fields.get("rcvtime")                 # string in ISO format
        try :                                           # parse ISO date/time
            timestamp = datetime.datetime.strptime(rcvtime, "%Y-%m-%d %H:%M:%S")
            timestamp = maketimelocal(timestamp)        # convert to local time
            msgtime = msgutils.edittime(timestamp)      # "07:30 PM"
            msgdate = msgutils.editdate(timestamp)      # "March 12"
        except ValueError :                             # if conversion problem
            self.logger.error("Date conversion failed: %s" % (rcvtime,))
            msgdate = ""                                # no date
            msgtime = rcvtime                           # use raw value
        #   The server tries to extract some fields from the message,
        #   if it is in our "TO person @ location : body" format
        msgsmsbody = fields.get("smsbody", "(NO TEXT)") # unparsed body
        msgbody = fields.get("msgbody")                 # parsed body
        if not msgbody :                                # if no parsed body
            msgbody = msgsmsbody                        # use unparsed body
        msgbody = self.cleanhtml(msgbody)               # remove HTML escapes
        msgdeliverynote = fields.get("deliverat")       # where to deliver
        msgto = fields.get("deliverto")                 # recipient
        #   Get geolocation information (from SMS via XML) and append to "From".
        expandplaceabbrev(fields, "smsfromcountry", placenames.CODE_COUNTRY) 
        expandplaceabbrev(fields, "smsfromstate", placenames.CODE_STATE) 
        locfields = ["smsfromcity", "smsfromstate", "smsfromcountry"]
        loc = ", ".join([fields.get(k) for k in locfields if fields.get(k)])
        if loc != "" :                                  # if have location
            msgfrom += " IN %s" % (loc,)                # append to "From"
        msgitem = feedmanager.FeedItem(self, msgfrom, msgdate, 
            msgtime, None, msgbody)                     # build output item
        if msgdeliverynote :                            # fields the delivery people need
            msgitem.setnote(msgdeliverynote)
        if msgto :
            msgitem.setto(msgto)
        msgitem.serial = fields['serial']               # meg serial for completion
        self.logger.debug("New SMS message: %s" % (repr(fields),))
        self.inqueue.put(msgitem)                       # output message item
     
    def fetcherror(self, msgtxt, message) :             # report fetch error
        if message and len(str(message)) > 0:           # if useful exception info
            msgtxt += '. (' + str(message) + ')'        # add it
        msgtxt += '.'
        self.logwarning(msgtxt)                         # log
        return(msgtxt)
                   
    def cleanhtml(self, s)    :                         # clean out HTML esc
        for (pattern, rep) in Twiliofeed.HTMLREWRITES:  # apply all rewrite 
            s = pattern.sub(rep, s)                     # in sequence
            return(s)                                   # return string with
            
    def markitemsdone(self) :                           # mark any printed items as printed
        #   We mark items as printed only in the networking thread, so that we never
        #   stall the thread that runs the Teletype on a network error.
        try:
            item = None                                 # item to do
            while True:                                 # until Queue.empty or network error
                item = self.donequeue.get_nowait()      # get input if any
                serial = item.serial                    # serial number of done item
                reply = doservercmd(self.logger, self.accountsid, self.ourphoneno,
                    "printed", serial, serial)   
        except Queue.Empty:                             # if empty
            return(True)                                # success
        except IOError as message:                      # network error during cancel
            self.logerror(self.fetcherror("Network error recording message as printed", message))
            if item :                                   # if have item to do
                self.donequeue.put(item)                # put item back on queue
            return(False)

#
#   Unit test
#
def test(accountsid) :
    import logging
    import time
    logging.basicConfig()                               # configure logging system
    logger = logging.getLogger('Messager')              # main logger				
    logger.setLevel(logging.DEBUG)						# very verbose

    feed = Twiliofeed(accountsid, logger)
    feed.start()
    for i in xrange(300) :
        msg = feed.getitem()
        if msg :
            print("Got msg: %s" % (repr(msg),))         # got msg
            print("Message for display:\n" + msg.formattext())
            continue
        time.sleep(1)                                   # otherwise wait
    print("Test completed")
    
if __name__ == '__main__':
    test("ACfaad9c9689c601d5dc8cc7498ffb6ee2")


                                    
