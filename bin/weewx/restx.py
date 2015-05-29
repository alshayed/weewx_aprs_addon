
#==============================================================================
# APRS
#==============================================================================

class StdAPRS(StdRESTful):
    """Weewx service for posting using the APRS protocol.
    
    Manages a separate thread APRSThread"""

    def __init__(self, engine, config_dict):
        
        super(StdAPRS, self).__init__(engine, config_dict)
        
        # Extract the required parameters. If one of them is missing,
        # a KeyError exception will occur. Be prepared to catch it.
        try:
            # Extract a copy of the dictionary with the APRS options:
            _aprs_dict = get_dict(config_dict, 'APRS')
            _aprs_dict['station'] = _aprs_dict['station'].upper()
            
            # See if this station requires a passcode:
            if not _aprs_dict.has_key('output_file') or _aprs_dict['output_file'] == "":
                raise KeyError('output_file')
        except KeyError, e:
            syslog.syslog(syslog.LOG_DEBUG, "restx: APRS: Data will not be posted. "
                          "Missing option: %s" % e)
            return

        _database_dict= config_dict['Databases'][config_dict['StdArchive']['archive_database']]

        _aprs_dict.setdefault('latitude',  self.engine.stn_info.latitude_f)
        _aprs_dict.setdefault('longitude', self.engine.stn_info.longitude_f)
        _aprs_dict.setdefault('station_type', config_dict['Station'].get('station_type', 'Unknown'))
        self.archive_queue = Queue.Queue()
        self.archive_thread = APRSThread(self.archive_queue, _database_dict,
                                         **_aprs_dict)
        self.archive_thread.start()
        self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

        if not _aprs_dict.has_key('station'):
            _aprs_dict['station'] = ""

        if not _aprs_dict.has_key('aprs_path'):
            _aprs_dict['aprs_path'] = ""

        if _aprs_dict['station'] == "":
            syslog.syslog(syslog.LOG_INFO, "restx: APRS: Raw data will be posted to %s" % 
                          _aprs_dict['output_file'])
        else:
            syslog.syslog(syslog.LOG_INFO, "restx: APRS: Data for station %s will be posted to %s" % 
                          (_aprs_dict['station'], _aprs_dict['output_file']))

    def new_archive_record(self, event):
        self.archive_queue.put(event.record)

class APRSThread(RESTThread):
    """Concrete class for threads posting from the archive queue,
    using the APRS protocol."""

    def __init__(self, queue, database_dict, 
                 station, aprs_path, output_file,
                 latitude, longitude, station_type,
                 post_interval=600, max_backlog=sys.maxint, stale=1800,
                 log_success=True, log_failure=True,
                 timeout=10, max_tries=3, retry_wait=5):

        """
        Initializer for the APRSThread class.
        
        Required parameters:

          queue: An instance of Queue.Queue where the records will appear.
          
          database_dict: A dictionary holding the weedb database connection
          information. It will be used to open a connection to the archive 
          database.
          
          station: The name of the station. Something like "DW1234".

          aprs_path: The APRS path, ie WIDE1-1,WIDE2-1 or RFONLY

          output_file: The file where APRS data will be put, ie "/tmp/weewx-aprs.txt"
          
          latitude: Latitude of the station in decimal degrees.
          
          longitude: Longitude of the station in decimal degrees.
          
          station_type: The type of station. Generally, this is the driver
          symbolic name, such as "Vantage".
          
        Optional parameters:
        
          log_success: If True, log a successful post in the system log.
          Default is True.
          
          log_failure: If True, log an unsuccessful post in the system log.
          Default is True.
          
          max_backlog: How many records are allowed to accumulate in the queue
          before the queue is trimmed.
          Default is sys.maxint (essentially, allow any number).
          
          max_tries: How many times to try the post before giving up.
          Default is 3
          
          stale: How old a record can be and still considered useful.
          Default is 1800 (a half hour).
          
          post_interval: How long to wait between posts.
          Default is 600 (every 10 minutes).
          
          timeout: How long to wait for the server to respond before giving up.
          Default is 10 seconds.        
        """        
        # Initialize my superclass
        super(APRSThread, self).__init__(queue,
                                         protocol_name="APRS",
                                         database_dict=database_dict,
                                         post_interval=post_interval,
                                         max_backlog=max_backlog,
                                         stale=stale,
                                         log_success=log_success,
                                         log_failure=log_failure,
                                         timeout=timeout,
                                         max_tries=max_tries,
                                         retry_wait=retry_wait)
        self.station       = station
        if aprs_path == "":
            self.aprs_path     = "APRS"
        else:
            self.aprs_path     = "APRS,%s" % aprs_path
        self.output_file   = output_file
        self.latitude      = to_float(latitude)
        self.longitude     = to_float(longitude)
        self.station_type  = station_type

    def process_record(self, record, archive):
        """Process a record in accordance with the APRS protocol."""
        
        # Get the full record by querying the database ...
        _full_record = self.get_record(record, archive)
        # ... convert to US if necessary ...
        _us_record = weewx.units.to_US(_full_record)
        # ... get the login and packet strings...
        _tnc_packet = self.get_tnc_packet(_us_record)
        # ... then post them:
        self.send_packet(_tnc_packet)

    def get_tnc_packet(self, record):
        """Form the TNC2 packet used by APRS."""

        # Preamble to the TNC packet:
        if self.station == "":
            _prefix = ""
        else:
            _prefix = "%s>%s:" % (self.station,self.aprs_path)

        # Time:
        _time_tt = time.gmtime(record['dateTime'])
        _time_str = time.strftime("@%d%H%Mz", _time_tt)

        # Position:
        _lat_str = weeutil.weeutil.latlon_string(self.latitude,
                                                 ('N', 'S'), 'lat')
        _lon_str = weeutil.weeutil.latlon_string(self.longitude,
                                                 ('E', 'W'), 'lon')
        _latlon_str = '%s%s%s/%s%s%s' % (_lat_str + _lon_str)

        # Wind and temperature
        _wt_list = []
        for _obs_type in ['windDir', 'windSpeed', 'windGust', 'outTemp']:
            _v = record.get(_obs_type)
            _wt_list.append("%03d" % _v if _v is not None else '...')
        _wt_str = "_%s/%sg%st%s" % tuple(_wt_list)

        # Rain
        _rain_list = []
        for _obs_type in ['hourRain', 'rain24', 'dayRain']:
            _v = record.get(_obs_type)
            _rain_list.append("%03d" % (_v * 100.0) if _v is not None else '...')
        _rain_str = "r%sp%sP%s" % tuple(_rain_list)

        # Barometer:
        _baro = record.get('altimeter')
        if _baro is None:
            _baro_str = "b....."
        else:
            # While everything else in the APRS protocol is in US Customary,
            # they want the barometer in millibars.
            _baro_vt = weewx.units.convert((_baro, 'inHg', 'group_pressure'),
                                           'mbar')
            _baro_str = "b%05d" % (_baro_vt[0] * 10.0)

        # Humidity:
        _humidity = record.get('outHumidity')
        if _humidity is None:
            _humid_str = "h.."
        else:
            _humid_str = ("h%02d" % _humidity) if _humidity < 100.0 else "h00"

        # Radiation:
        _radiation = record.get('radiation')
        if _radiation is None:
            _radiation_str = ""
        elif _radiation < 1000.0:
            _radiation_str = "L%03d" % _radiation
        elif _radiation < 2000.0:
            _radiation_str = "l%03d" % (_radiation - 1000)
        else:
            _radiation_str = ""

        # Station equipment
        _equipment_str = ".weewx-%s-%s" % (weewx.__version__, self.station_type)
        
        _tnc_packet = ''.join([_prefix, _time_str, _latlon_str, _wt_str,
                               _rain_str, _baro_str, _humid_str,
                               _radiation_str, _equipment_str, "\n"])

        return _tnc_packet

    def send_packet(self, tnc_packet):

        with open(self.output_file, 'w') as f:
            f.write(tnc_packet)

