================

Changelog
=========

0.4.7 (2024-09-03)
------------------

* Added support for reading temperatures and door state
* Added wait_for_response - synchronous waiting for a specific response
* Refactoring

0.4.6 (2024-08-23)
------------------

* queue commands and synchronise sending with received responses
* all types of commands are grouped now (turn on, turn-off, arm, etc)
* refactoring
* cleanup
* more test cases

0.4.5 (2023-10-28)
------------------

* Fixed encoding 0xFE bytes in the message body

0.4.0 (2023-10-25)
------------------

* Syncing Satel commands

0.3.7 (2022-07-05)
------------------

* Integrated fix for Python 3.10 compatibility

0.3.3 (2019-03-07)
------------------

* Added ENTRY_TIME status to display "DISARMING" status in HA
* Fixed issue with unhandled connection error  causing HomeAssistant to give up on coommunication with eth module completely

0.3.2 (2019-02-18)
------------------

* Fixed status issues
* Introduced "pending status"

0.3.1 (2019-02-13)
------------------

* improved robustness when connection disapears
* fixed issues with "status unknown" which caused blocking of the functionality in HA
- still existing issues with alarm status - to be fixed

0.2.0 (2018-12-20)
------------------

* Integrated changes from community: added monitoring of ouitputs.
* Attempt at fixing issue with "state unknown" of the alarm. Unfurtunately unsuccesful.

0.1.0 (2017-08-24)
------------------

* First release on PyPI.
