# Splunk-Flurry Connector

> **Currently broken**: Flurry has updated their private API again in a way that breaks this app. Please see <https://github.com/splunk/splunk-app-flurry/issues/5>

> **Seeking new maintainer**: I no longer have the cycles to maintain this app myself, but I will review patches that are submitted against it.

This app provides a scripted input for [Splunk](http://www.splunk.com/) that automatically extracts event logs from [Flurry](http://www.flurry.com/). It is tested on Splunk 4.3.2.

This input permits the use of Splunk's advanced data analysis capabilities on Flurry data. This is particularly useful if your application logs custom events and you want to perform custom analyses on them.


## Installation

* Install the app by copying the `flurry` directory to `$SPLUNK_HOME/etc/apps/flurry`.

* (Re)start Splunk so that the app is recognized.

* In the Splunk web interface, from the App menu, select the Flurry app. And press "Continue to app setup page".

* Input your Flurry credentials and the earliest date containing events you wish to download. Enable the Flurry input. Click "Save".

* Wait 15 seconds or so for the new scripted input to extract the first couple of events.

* Run a test search `index=flurry` to see the events.

  * If you don't see any events, open the log `$SPLUNK_HOME/var/log/splunk/splunkd.log` and look for errors issued by ExecProcessor related to the `$SPLUNK_HOME/etc/apps/flurry/bin/extract.py` script. Most likely, the configured authentication credentials or the project ID was incorrect.


## Example Searches

The following searches are installed by default:

* **Activity by Time of Day**:
  `index=flurry | stats count by date_hour | sort +date_hour`

* **Activity by Day of Week**:
  `index=flurry | eval weekday=strftime(_time, "%w") | stats count, values(date_wday) as weekday_name by weekday | sort +weekday | table weekday_name, count`

* **Top Users**:
  `index=flurry Session_Index=1 | top User_ID`

If you log location information in events, you can use, in combination with the [Google Maps](http://splunk-base.splunk.com/apps/22365/google-maps) add-on:

* **Activity by Location**:
  `index=flurry EVENT_NAME__UserLocation=* | eval _geo=EVENT_NAME__UserLocation`

If you log OS information in events, you can use:

* **Activity by OS Version**:
  `index=flurry EVENT_NAME__SystemVersion=* | stats count by EVENT_NAME__SystemVersion | sort +EVENT_NAME__SystemVersion`


## License

This software is licensed under the Apache License 2.0.
Details can be found in the file LICENSE.

This software includes [Mechanize](http://wwwsearch.sourceforge.net/mechanize/) as a source dependency, which is used under the [BSD License](http://www.opensource.org/licenses/bsd-license.php).
