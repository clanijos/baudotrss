#
#   nwsweatherreport  --  get and parse weather report from National Weather Service
#
#   Weather reports are in Digital Weather Markup Language, an XML format.  The schema for them is
#   http://graphical.weather.gov/xml/DWMLgen/schema/DWML.xsd
#
from six.moves import urllib                # Python 2/3 support
import xml
import xml.etree
import xml.etree.ElementTree
import datetime
import calendar
import re
import msgutils
import placenames                           # spell out state
import traceback
#
try :                                       # Python 3 obnoxiousness
    unicode("a")
except:
    unicode = str                           # they could have made it a synonym, but no
#
#   Constants
#
#   Prototype URL for fetching weather via latitude and longitude
NWSPROTOURL = "http://forecast.weather.gov/MapClick.php?lat=%1.4f&lon=%1.4f&unit=0&lg=english&FcstType=dwml"
#
#   Utility functions
#
#
#   prettify -- convert to string
#
def prettify(tree) :
    """
    Convert to string.
    Portable for Python 2/3
    No line breaks or indentation, unfortunately.
    """
    return(xml.etree.ElementTree.tostring(tree, encoding="utf8").decode("utf8"))   # portable for Python 2/3
     
def gettextitem(tree, key) :
    """
    Look for <key>text</key> in tree and return simple text.
    """
    item = tree.find(key)                   # find containing XML item
    if item is None :                       # if no find
        raise RuntimeError('XML document did not contain an expected "%s" item within "%s".' % (key, tree.tag))
    text = item.text                        # find text contained in XML item      
    if text is None :                       # if no find
        raise RuntimeError('XML document did not contain text for an expected "%s" item.' % (key,))
    return(text.strip())
#
#   findfirst
#
def findfirst(tree, key) :
    """
    Find first matching item at any depth
    
    bs4 has this, but ElementTree does not
    """
    for item in tree.iter(key) :            # depth first search
        return(item)                        # first hit
    return(None)
#
#   class UTC -- a time zone item for UTC
#
#   This is in Python 3.x in a different form, but not in 2.7
#
class UTC(datetime.tzinfo) :
    def utcoffset(self, d) :                # zero offset from UTC
        return(datetime.timedelta(0))
    def dst(self, sink) :                   # no Daylight Savings Time for UTC
        return(None)
        
timezoneutc = UTC()                         # singleton
        
def parseisotime(s) :
    """
    Parse subset of ISO 8601 time format, including time zone.
    
    Result is "aware" datetime object, in UTC.
    """
    s = s.strip()
    datetimepart = s[0:19]                  # extract fixed-length time part (will fail if microseconds)
    offsetpart = s[19:]                     # extract time zone offset
    dt = datetime.datetime.strptime(datetimepart,"%Y-%m-%dT%H:%M:%S")   # datetime, naive.
    if (len(offsetpart) > 0) :              # if have offset
        if offsetpart.upper() == "Z" :      # if Zulu time
            offset == 0
        else :
            fields = re.match(r'([+-])(\d\d):(\d\d)', offsetpart)
            if fields is None :
                raise RuntimeError("Unable to parse time string: %s" % (s,))
            sign = fields.group(1)          # + or -
            hours = int(fields.group(2))    # has to be valid number, passed RE
            minutes = int(fields.group(3))
            offset = (hours*60 + minutes)   # compute unsigned value of offset
            if sign == "-" :                # if - sign
                offset = -offset            # invert offset
            #   Now have offset in minutes.  Apply to dt.
        #   Apply time zone offset
        dt -= datetime.timedelta(minutes=offset) # apply offset
        dt = dt.replace(tzinfo=timezoneutc) # make this an "aware" object
    return(dt)
#
#   class nwsperiod 
#
class nwsperiod(object) :
    """
    National Weather Service forecast data for one period
    """
    
    def __init__(self, timeinfo, forecasttext) :
        """
        Set time and text of a forecast
        """
        self.err = None
        self.timeinfo = timeinfo                    # (timestamp, time name)
        self.forecasttext = forecasttext            # text of forecast
        
    def hoursinfuture(self, forecasttime) :
        """
        How far ahead is this forecast looking, in hours?
        """
        timediff = self.timeinfo[0] - forecasttime  # how far ahead is this?
        secsahead = timediff.days * 86400 + timediff.seconds  # seconds ahead
        return (secsahead / 3600.0)                 # hours ahead
        
        
    def asString(self) :
        """
        Display weather forecast for a period as a string
        """
        (timestamp, timename) = self.timeinfo       # unpack time info
        # convert to local time from UTC timestamp
        localforecasttime = datetime.datetime.fromtimestamp(calendar.timegm(timestamp.timetuple()))
        s = msgutils.editdate(localforecasttime)    # date only as "May 1"
        if timename is None  :                      # Usually have "Tueseday evening", etc. from NWS
            timename = msgutils.edittime(localforecasttime) # if not, use actual time
        s = timename + ", " + s                     # prefix it to date
        return("%s: %s" % (s, self.forecasttext))   # human-readable string
        
        
#
#   class nwsxml  --  National Weather Service XML in useful format
#
class nwsxml(object) :
    """
    National Weather Service data from XML
    """
    def __init__(self, verbose = False) :
        self.verbose = verbose                  # true if verbose
        self.err = None                         # no errors yet
        self.location = None                    # printable city, state
        self.creationtime = None                # date/time of forecast
        self.latitude = None                    # latitude (string)
        self.longitude = None                   # longitude (string)
        self.perioditems = []                   # forecast items for time periods
        
    def _find(self, tree, keys) :
        """
        Find item at tuple of keys given.
        Raises RuntimeError if fail
        """
        assert(isinstance(keys, tuple))
        assert(len(keys) > 0)
        key = keys[0]
        item = tree.find(key)                               # look for indicated tag
        if item is None :
            raise RuntimeError('NWS weather report XML format unexpected. Did not find "%s" inside XML tag "%s"' % (key, tree.tag))
        if len(keys) < 2 :                                  # if more keys
            return(item)                                    # done
        return(self._find(item, keys[1:]))                  # tail recurse for next tag          
            
                
    def _parseheader(self, tree) :
        """
        Parse forecast header info - location, time, etc.
        """
        #   Make sure this is a proper forecast document
        if tree.tag == "dwml" :
            dwmlitem = tree
        else :
            dwmlitem = self._find(tree,("dwml",))           # should be Digital Weather Markup Language
        if dwmlitem is None :                               # This isn't a valid weather report
            msg = "Weather forecast not found"              # note problem
            titleitem = tree.find("title")                  # probably HTML
            if titleitem :                                  # pull title from HTML if present
                titletext = titleitem.text
                if titletext is not None:
                    msg = titletext
            raise RuntimeError(msg)                         # fails
        #   Process header. Looking for <dwml><head><product>
        productitem = self._find(dwmlitem, ("head","product"))
        #   Get creation date/time
        creationitem = self._find(productitem,("creation-date",))  # timestamp item 
        creationtime = creationitem.text                    # get period text, which is a timestamp
        if creationtime is None :
            raise RuntimeError("No forecast creation date/time")
        self.creationtime = parseisotime(creationtime.strip()) # convert to timestamp
        #   Process data.  Looking for <dwml><data type=forecast>
        #   Get location name
        dataitem = self._find(dwmlitem, ("data",))
        if dataitem.attrib.get("type") != "forecast" :
            raise RuntimeError("No forecast data item")
        locitem = self._find(dataitem,("location",))        # expecting <location><point>
        pointitem = self._find(locitem,("point",))          # find point item
        self.latitude = pointitem.attrib.get("latitude")    # get fields of interest
        self.longitude = pointitem.attrib.get("longitude")
        cityitem = locitem.find("city")                     # must have <city> or <area-description>
        if cityitem is not None :
            state = cityitem.attrib.get('state')
            city = cityitem.text                            # get city name
            if city is None:
                raise RuntimeError("No city name")
            state = placenames.CODE_STATE.get(state, state) # spell out state name if possible
            self.location = city + ", " + state
        else :                                              # no city, use NWS area description
            areaitem = self._find(locitem,("area-description",))     # go for area description
            area = areaitem.text                            # "6 Miles ESE Hidden Valley Lake CA"
            if area is None :
                raise RuntimeError("No area description")
            self.location = area                            # use NWS area description
      
    def _parsetimeitems(self, key, timeitems) :
        """
        Parse time items within a time layout.  Return time item list or None
        Time item list is [(timestamp, timeastext),..]
        """
        timeitemlist = []                                   # accumulate time items here
        for timeitem in timeitems :                         # for time items in this set         
            periodname = timeitem.attrib.get("period-name", None) # get period name
            periodtime = timeitem.text                      # get period text, which is a timestamp
            if periodtime is None :
                raise RuntimeError("No period date/time in time item")
            if periodtime.strip() == "NA" :                 # if any time not available
                if self.verbose :                           # discard entire time layout
                    print("Found NA item in time item list for key '%s'" % (key,))
                return(None)
            periodtime = parseisotime(periodtime.strip())   # should convert to timestamp
            timeitemlist.append((periodtime, periodname))   # Nth entry for this time layout
        return(timeitemlist)                                # success, have list      
    
    def _parsetimelayouts(self, tree) :
        """
        Find and index time layouts by time layout key.  Returns 
        { key: (perioddatetime, periodname), ... }
        """
        timelayouts = {}                                    # key, parse tree
        timelayouttrees = tree.iter("time-layout")          # find all time layouts
        for timelayouttree in timelayouttrees :             # for all trees
            keytag = self._find(timelayouttree,("layout-key",))
            key = keytag.text                               # get text
            if key is None :                                # must be a single text item
                raise RuntimeError("No time layout key found in time layout")
            key = key.strip()                               # clean up key
            timeitemlist = self._parsetimeitems(key, timelayouttree.iter('start-valid-time'))    
            if timeitemlist :                               # if got list
                timelayouts[key] = timeitemlist             # item for this key
                if self.verbose :
                    print("Time layout '%s': %s" % (key, unicode(timeitemlist)))
        return(timelayouts)
        
    def _parseforecasts(self, tree, timelayouts) :
        """
        Find text forecast.  Each forecast has an associated time layout name.
        The time layout is a separate item which associates timestamps with
        the forecast.
        """
        wordedforecasts = tree.iter("wordedForecast")       # find forecasts
        if wordedforecasts is None :
            raise RuntimeError("Forecast text not found in data")
        for wordedforecast in wordedforecasts :             # for each forecast
            timelayoutkey = wordedforecast.attrib.get("time-layout", None)   # get time layout name
            if timelayoutkey is None :
                raise RuntimeError("Forecast time layout key not found in data")
            forecasttextitems = wordedforecast.iter("text") # get text items
            forecasttexts = []                              # text items
            for forecasttextitem in forecasttextitems :     # for all text items
                s = forecasttextitem.text.strip()           # get text item                   
                forecasttexts.append(s)                     # save forecast text
            #   Now find matching time layout item for forecast
            timelayoutkey = timelayoutkey.strip()
            if not (timelayoutkey in timelayouts) :         # if time layout not on file for this key
                raise RuntimeError("Time layout key '%s' not found in time layouts" % (timelayoutkey,))
            timelayout = timelayouts[timelayoutkey]         # get relevant layout
            #   The number of time layouts and the number of forecast texts has to match
            if len(timelayout) != len(forecasttexts) :
                if (self.verbose) :
                    print("Time layout: %s" % (unicode(timelayout,)))
                    print("Forecasts: %s" % (unicode(forecasttexts,)))
                raise RuntimeError("Time layout key '%s' has %d forecast times, but there are %d forecasts" %
                    (timelayoutkey, len(timelayout), len(forecasttexts)))
            #   We have a good set of forecasts and time stamps.    
            if self.verbose :
                print("Forecast time layout key %s matches time layout %s" % (timelayoutkey, unicode(timelayout)))          
            for i in range(len(timelayout)) :
                    self.perioditems.append(nwsperiod(timelayout[i], forecasttexts[i]))   # new forecast item

  
        
    def parse(self, tree) :
        """
        Take in XML parse tree of XML forecast and update object.
        """
        try :
            #   Get forecast 
            self._parseheader(tree)                         # parse header (location, time, etc.)
            timelayouts = self._parsetimelayouts(tree)      # get time layouts needed to timestamp forecasts
            self._parseforecasts(tree, timelayouts)         # parse forecasts
                
        except (EnvironmentError, RuntimeError, xml.etree.ElementTree.ParseError) as message :
            self.err = "Unable to interpret weather data: %s." % (message,)
            return
            
        
    def asString(self, hoursahead = 99999999) :
        """
        Return object as useful string.
        
        hoursahead limits how far ahead the forecast will be reported.
        """
        if self.err :
            return("ERROR: %s" % (self.err,))
        if self.verbose :
            print("Forecast creation time: " + self.creationtime.isoformat()) 
        # convert to local time from UTC timestamp
        localforecasttime = datetime.datetime.fromtimestamp(calendar.timegm(self.creationtime.timetuple()))
        timemsg = "%s at %s" % (msgutils.editdate(localforecasttime), msgutils.edittime(localforecasttime))
        s = "Weather forecast for %s on %s.\n\n" % (self.location, timemsg)   # header line
        return(s + "\n\n".join(
            [x.asString() for x in self.perioditems if x.hoursinfuture(self.creationtime) < hoursahead]))
#
#   getnwsforecast -- get National Weather Service forecast for lat, lon
#
#   Synchronous.  Result as text
#
def getnwsforecast(lat, lon, verbose=False) :
    url = NWSPROTOURL % (lat, lon)
    if verbose :
        print("NWS url: %s" % url)          # show URL
    try:
        opener = urllib.request.urlopen(url)    # URL opener object 
        xmltext = opener.read()                 # read entire contents
        opener.close()                          # close
        tree = xml.etree.ElementTree.fromstring(xmltext) # parse
        if verbose :
            print(prettify(tree))               # print tree for debug
        forecast = nwsxml(verbose)              # get new forecast
        forecast.parse(tree)                    # parse forecast
        return(forecast.asString(72))           # return result 
    except IOError as message :                 # if trouble
        s = "Unable to get weather forecast: " + str(message)
        return(s)
 
#
#   getziplatlong --  get latitude and longitude given ZIP code.
#
#   Service by NWS
#
NWSZIPURL = "http://graphical.weather.gov/xml/sample_products/browser_interface/ndfdXMLclient.php?listZipCodeList=%s"
NWSZIPRE = re.compile(r'\s*([+-]?\d+\.\d*)\s*,\s*([+-]?\d+\.\d*)\s*')   # matches 123.45,-345.23
#
#   getziplatlong  
#
def getziplatlong(zip, verbose=False) :
    """
    Get latitude and longitude for a US ZIP code
    
    Uses NWS NDFD service.
    """
    url = NWSZIPURL % (urllib.parse.quote_plus(zip),)
    if verbose :
        print("NWS ZIP lookup url: %s" % (url,))          # show URL
    try:
        opener = urllib.request.urlopen(url)    # URL opener object 
        xmltext = opener.read()                 # read entire contents
        opener.close()                          # close
        tree = xml.etree.ElementTree.fromstring(xmltext)
        if verbose :
            print(prettify(tree))               # print tree for debug
        latlon = gettextitem(tree, "latLonList")# look for lat lon item
        #   Format of latLon is number, number
        matches = NWSZIPRE.match(latlon)        # looking for 123.45,-345.23
        if matches is None:
            raise RuntimeError("ZIP code lookup found no result.")
        lat = matches.group(1)
        lon = matches.group(2)
        return((None, lat, lon))                # returns (msg, lat, lon)
    except (RuntimeError, EnvironmentError, xml.etree.ElementTree.ParseError) as message :                 # if trouble
        s = "Unable to get location of ZIP %s: %s" % (zip, str(message))
        return((s, None, None))

#        
#   USGS place name lookup
#
USGSGNISURL = "http://geonames.usgs.gov/pls/gnis/x?fname='%s'&state='%s'&cnty=&cell=&ftype='Civil'&op=1"  
#
#   getplacelatlong  
#
def getplacelatlong(city, state, verbose=False) :
    """
    Get latitude and longitude for a US place name.
    
    Uses USGS GINS service.
    """
    state = placenames.CODE_STATE.get(state, state)     # USGS requires state name, not abbreviation 
    url = USGSGNISURL % (urllib.parse.quote_plus(city), urllib.parse.quote_plus(state))
    if verbose :
        print("USGS url: %s" % (url,))          # show URL
    try:
        opener = urllib.request.urlopen(url)    # URL opener object 
        xmltext = opener.read()                 # read entire contents
        opener.close()                          # close
        tree = xml.etree.ElementTree.fromstring(xmltext)
        if verbose :
            print(prettify(tree))               # print tree for debug
        features = tree.iter("USGS")            # find all USGS features
        bestfeaturename = None                  # pick best match name
        lat = None
        lng = None
        for feature in features :               # find best matching name
            featurename = gettextitem(feature,"FEATURE_NAME")
            if (bestfeaturename is None or      # pick either first or exact match
                (city.upper() == featurename.upper())) :
                bestfeaturename = featurename
                lat = gettextitem(feature,"FEAT_LATITUDE_NMBR")
                lng = gettextitem(feature,"FEAT_LONGITUDE_NMBR")
        if bestfeaturename is None :
            raise RuntimeError("City not found")
        return((None, bestfeaturename, lat, lng))

    except (RuntimeError, EnvironmentError, xml.etree.ElementTree.ParseError) as message :                 # if trouble
        s = "Unable to get location of %s, %s: %s" % (city, state, str(message))
        traceback.print_exc()   # ***TEMP***
        return((s, None, None, None))
#
#   getweatherreport  -- main interface
#
def getweatherreport(city, state, zip) :
    """
    Get weather report, given city, state, zip info.
    """
    if zip :                                    # if have ZIP code
        (msg, lat, lon) = getziplatlong(zip)    # look up by ZIP code
    elif city and state :                       # look up by city, state
        (msg, place, lat, lon) = getplacelatlong(city, state)
    else :                                      # no location
        msg = "No location configured for weather reports."
    if msg :
        return(msg)
    return(getnwsforecast(float(lat),float(lon)))  # return actual forecast
   
#
#   Unit test
#
#
#   testcity
#
def testcity(city, state, verbose=False) :
    print("Test city: %s, %s." % (city, state))
    loc = getplacelatlong(city, state, verbose)
    (msg, place, lat, lon) = loc
    if msg :
        print("ERROR: " + msg)
    else :
        s = getnwsforecast(float(lat), float(lon), verbose)
        print(s)
    print("")    
#
#   testzip
#
def testzip(zip, verbose=False) :
    print("Test ZIP: %s." % (zip,))
    loc = getziplatlong(zip, verbose)
    (msg, lat, lon) = loc
    if msg :
        print("ERROR: " + msg)
    else :
        s = getnwsforecast(float(lat), float(lon), verbose)
        print(s)
    print("")  
        
if __name__== "__main__" :                      # if unit test 
    lat = 37.7749295                            # San Francisco, CA
    lon= -122.4194155
    s = getnwsforecast(lat, lon, True)
    print(s)
    lat = 38.7749295                            # Near Konocti
    lon = -122.4194155
    s = getnwsforecast(lat, lon, False)
    print(s)
    #   Look up by ZIP
    testzip("22204", True)
    testzip("94062", False)
    #   Look up by city
    testcity("San Francisco", "CA", True)
    testcity("City of San Jose", "CA", True)
    testcity("New York", "NY", False)
    testcity("Athens", "GA", False)
 
