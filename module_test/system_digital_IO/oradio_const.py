'''

  ####   #####     ##    #####      #     ####
 #    #  #    #   #  #   #    #     #    #    #
 #    #  #    #  #    #  #    #     #    #    #
 #    #  #####   ######  #    #     #    #    #
 #    #  #   #   #    #  #    #     #    #    #
  ####   #    #  #    #  #####      #     ####


Created on Jan 17, 2025
@author:        Henk Stevens & Olaf Mastenbroek & Onno Janssen
@copyright:     Copyright 2024, Oradio Stichting
@license:       GNU General Public License (GPL)
@organization:  Oradio Stichting
@version:       1
@email:         oradioinfo@stichtingoradio.nl
@status:        Development
@summary:       Defines for oradio scripts
'''

############ ERRORS ############################################
NO_HW_VERSION_FILE        = "error: no hw version file found"
NO_CONFIG_FILE            = "error: no config file"
CONFIG_FILE_EXISTS        = "config file exists"

############ BUTTON STATES ################################
SHORT_PRESS_BUTTON         = "short_press_button"
LONG_PRESS_BUTTON_DETECTED = "long_press_button_detected"
LONG_PRESS_BUTTON          = "long_press_button"
LONG_PRESS_TOO_SHORT       = "long press too short"
BUTTON_NONE                = "None"
VOLUME_ROTATED             = "volume rotated"
BUTTON_DETECTED            = 'button detected'

######### BUTTON RETURN VALUES #################
CHECK_FOR_LONG_DETECTION = 'check for long detection'
CHECK_FOR_LONG           = 'check for long'
BUTTON_HANDLING_DONE     = 'button handling done'
BUTTON_HANDLING_WARNING  = 'button handling warning'

########## LEDS ######
LED_ON      = "led_ON"
LED_OFF     = "led_OFF"
LED_LEFT    = "led_LEFT"
LED_MIDDLE  = "led_MIDDLE"
LED_RIGHT   = "led_RIGHT"
