#!/usr/bin/env python
#
# Copyright 2011-2012 Splunk, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"): you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""A scripted input that downloads event logs from the Flurry service."""

import csv
from datetime import date, timedelta
from HTMLParser import HTMLParser
import logging
import mechanize
import re
import splunk
import splunklib.client
import sys
from time import sleep

CONFIG_FILENAME = 'extract'
CONFIG_KEYS_NEEDING_REPLACEMENT = (
    ('auth', ('email', 'password', 'project_id')),
    ('extract_position', ('year', 'month', 'day'))
)

class ConfigError(Exception):
    pass

class RateLimitedError(Exception):
    pass

class FlurryConnection(object):
    def __init__(self, email, password, project_id):
        """
        :param email: email used to login.
        :param password: password used to login.
        :param project_id: identifier for the application, obtained from the
                           URL of the analytics dashboard page after logging in
                           with a real web browser.
        """
        self.email = email
        self.password = password
        self.project_id = project_id
    
    def login(self):
        """
        Logs in to Flurry.
        
        This should be invoked before any other methods.
        Repeated invocations will create a new login session.
        
        :raises Exception: if login fails for any reason.
        """
        self.browser = mechanize.Browser()
        self.browser.open('https://dev.flurry.com/secure/login.do')
        self.browser.select_form(name='loginAction')
        self.browser['loginEmail'] = self.email
        self.browser['loginPassword'] = self.password
        resp = self.browser.submit()
        
        resp_url = resp.geturl()
        success = (
            resp_url.startswith('https://dev.flurry.com/home.do') or
            (resp_url.startswith('https://dev.flurry.com/fullPageTakeover.do')
                and 'home.do' in resp_url))
        if not success:
            raise Exception("Couldn't login to Flurry. Redirected to %s." % 
                resp_url)
        return resp
    
    def download_log(self, yyyy, mm, dd, offset):
        """
        Downloads an individual page of events that occurred on the specified
        day, returning a file-like object containing CSV data.
        
        If the page does not exist, the returned CSV will not have any data
        rows. However it will contain an initial header row.
        
        :param yyyy: year.
        :param mm: month (1 = January, 12 = December).
        :param dd: day.
        :param offset: index of the first session that will be returned.
        
        :raises RateLimitedError: if Flurry denies access due to too many
                                  requests in a short time frame.
        :raises Exception: if the download fails for any other reason.
        """
        url = ('https://dev.flurry.com/eventsLogCsv.do?projectID=%d&' + 
            'versionCut=versionsAll&intervalCut=customInterval' + 
            '%04d_%02d_%02d-%04d_%02d_%02d&direction=1&offset=%d') % (
                self.project_id, yyyy, mm, dd, yyyy, mm, dd, offset)
        resp = self.browser.open(url)
        
        redirect_url = self.browser.geturl()
        if redirect_url != url:
            if redirect_url == 'http://www.flurry.com/rateLimit.html':
                raise RateLimitedError
            else:
                raise Exception('Redirected to unexpected location while ' + 
                    'downloading event logs: %s.' % redirect_url)
        
        return resp

UNESCAPER = HTMLParser()

def parse_params(params):
    params = params.strip('{}')
    if len(params) == 0:
        return []
    
    params_split = params.split(' : ')
    
    # Process intermediate elements (not ends)
    params_flat = []
    params_flat.append(params_split[0])
    for i in xrange(1, len(params_split) - 1):
        param_split = params_split[i].rsplit(',', 1)
        if len(param_split) != 2:
            raise Exception('Could not parse intermediate parameter fragment: %s' % repr(param_split))
        params_flat.extend(param_split)
    params_flat.append(params_split[-1])
    
    if len(params_flat) % 2 != 0:
        raise Exception('Expected even number of keys and values: %s' % repr(params_flat))
    
    params = []
    for i in xrange(0, len(params_flat), 2):
        params.append((params_flat[i], params_flat[i + 1]))
    
    params = [(k.decode('utf-8'), v.decode('utf-8')) for (k, v) in params]
    # (unescape() expects Unicode strings)
    params = [(UNESCAPER.unescape(k), UNESCAPER.unescape(v)) for (k, v) in params]
    params = [(k.encode('utf-8'), v.encode('utf-8')) for (k, v) in params]
    
    return params

INVALID_KEY_CHAR_RE = re.compile(r'[^a-zA-Z0-9_]')

def quote_k(k):
    # Clean the key using Splunk's standard "key cleaning" rules
    # See: http://docs.splunk.com/Documentation/Splunk/4.3.2/Knowledge/
    #      Createandmaintainsearch-timefieldextractionsthroughconfigurationfiles
    return INVALID_KEY_CHAR_RE.sub('_', k)

def quote_v(v):
    return '"' + v.replace('"', "'") + '"'

def setup_logger():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
    logging.root.addHandler(handler)
    return logging.root

class SplunkConfigFile(object):
    def __init__(self, owner, namespace, conf_name, session_key):
        # NOTE: Requires 'develop' version of splunklib past 0.8.0
        #       for 'token' parameter to be honored.
        service = splunklib.client.Service(
            host=splunk.getDefault('host'),
            port=splunk.getDefault('port'),
            scheme=splunk.getDefault('protocol'),
            owner=owner,
            app=namespace,
            token='Splunk %s' % session_key)
        
        self._stanzas = {}
        for stanza in service.confs[conf_name]:
            self._stanzas[stanza.name] = stanza
    
    def get(self, stanza, key):
        return self._stanzas[stanza].content[key]
    
    def set(self, stanza, key, value):
        self._stanzas[stanza].content[key] = value
    
    def flush(self, stanza):
        stanza = self._stanzas[stanza]
        stanza.update(**stanza.content)

# -----------------------------------------------------------------------------

output = sys.stdout

log = setup_logger()
log.setLevel(logging.WARNING)   # for more messages, set to: logging.INFO

# Read session key from splunkd
session_key = sys.stdin.readline().strip()
if len(session_key) == 0:
    sys.stderr.write('Did not receive a session key from splunkd. Please enable passAuth in inputs.conf for this script\n')
    exit(1)

config = SplunkConfigFile('nobody', 'flurry', CONFIG_FILENAME, session_key)

# Ensure configuration looks valid
for (section, keys) in CONFIG_KEYS_NEEDING_REPLACEMENT:
    for key in keys:
        value = config.get(section, key)
        if value.startswith('__') and value.endswith('__'):
            raise ConfigError('Missing configuration value %s/%s in %s.conf' %
                (section, key, CONFIG_FILENAME))

did_login = False
rate_limited_on_last_request = False
while True:
    (year, month, day, offset) = (
        int(config.get('extract_position', 'year')),
        int(config.get('extract_position', 'month')),
        int(config.get('extract_position', 'day')),
        int(config.get('extract_position', 'offset')))
    
    # Since Flurry returns events in reverse chronological order,
    # it is difficult to download events in a streaming fashion
    # from the same day. Therefore only download up to the previous
    # day's events.
    cur_date = date(year, month, day)
    if cur_date >= date.today():
        log.info('All events extracted up to yesterday.')
        break
    
    if not did_login:
        conn = FlurryConnection(
            config.get('auth', 'email'),
            config.get('auth', 'password'),
            int(config.get('auth', 'project_id')))
        conn.login()
        did_login = True
    
    try:
        log.info('Downloading: %04d-%02d-%02d @ %d', year, month, day, offset)
        flurry_csv_stream = conn.download_log(year, month, day, offset)
    except RateLimitedError:
        if rate_limited_on_last_request:
            # Abort temporarily
            log.warning('Rate limited twice. Giving up.')
            break
        
        delay = float(config.get('rate_limiting', 'delay_per_overlimit'))
        log.info('Rate limited. Retrying in %s seconds(s).', delay)
        sleep(delay)
        
        conn.login()
        
        rate_limited_on_last_request = True
        continue
    else:
        rate_limited_on_last_request = False
    
    try:
        flurry_csv = csv.reader(flurry_csv_stream)
        
        col_names = flurry_csv.next()
        col_names = [col.strip() for col in col_names]  # strip whitespace
        expected_col_names = [
            'Timestamp', 'Session Index', 'Event', 'Description', 'Version',
            'Platform', 'Device', 'User ID', 'Params']
        assert col_names == expected_col_names
        
        cur_session_id = int(config.get('extract_position', 'session'))
        num_sessions_read = 0
        for row in flurry_csv:
            row = [col.strip() for col in row]  # strip whitespace
            
            # Pull apart the row
            (timestamp, session_index, event, description, version,
                platform, device, user_id, params) = row
            
            if session_index == '1':
                cur_session_id += 1
                num_sessions_read += 1
            
            # Output the original row data
            for i in xrange(len(col_names)):
                (k, v) = (col_names[i], row[i])
                output.write('%s=%s ' % (quote_k(k), quote_v(v)))
            
            # Append generated fields
            output.write('%s=%s ' % (quote_k('Session'), quote_v(str(cur_session_id))))
            
            # Break out the event parameters and output them individually
            for (k, v) in parse_params(params):
                k = '%s__%s' % (event, k)
                output.write('%s=%s ' % (quote_k(k), quote_v(v)))
            
            output.write('\r\n')
        
        output.flush()
        
        config.set('extract_position', 'session', str(cur_session_id))
        
        # Calculate next extraction offset
        if num_sessions_read > 0:
            # Potentially more sessions on the same day
            
            cur_offset = int(config.get('extract_position', 'offset'))
            next_offset = cur_offset + num_sessions_read
            
            config.set('extract_position', 'offset', str(next_offset))
            config.flush('extract_position')
        else:
            # All events on the current day have been read
            
            cur_date = date(
                int(config.get('extract_position', 'year')),
                int(config.get('extract_position', 'month')),
                int(config.get('extract_position', 'day')))
            
            next_date = cur_date + timedelta(days=1)
            
            config.set('extract_position', 'year', str(next_date.year))
            config.set('extract_position', 'month', str(next_date.month))
            config.set('extract_position', 'day', str(next_date.day))
            config.set('extract_position', 'offset', str(0))
            config.flush('extract_position')
    finally:
        flurry_csv_stream.close()
    
    # Delay between requests to avoid flooding Flurry
    sleep(float(config.get('rate_limiting', 'delay_per_request')))
