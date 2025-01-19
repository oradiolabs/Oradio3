# Simplified logging function - remove logging and monitoring, only print formatted log message
def logging(level, log_text):
    '''
    Simplified logging of log message
    :param level (str) - level of logging [ 'warning' | 'error' | 'info']
    :param log_text (str) - logging message
    '''
    # check whether rpi is throttled or running normal
    import inspect
    inspect_info = inspect.stack()
    module_info  = inspect.stack()[1]
    mod_name     = inspect.getmodule(module_info[0]).__name__
    frame_info   = inspect_info[1][0]
    func_name    = inspect.getframeinfo(frame_info)[2]

    # Build logging text
    logging_text = '{mnm:s} - {fnm:s} : {txt:s}'.format(mnm=mod_name, fnm=func_name, txt=log_text)
    
    RED_TXT     = "\x1b[31;20m"
    YELLOW_TXT  = "\033[93m"
    GREEN_TXT   = "\x1b[32m"
    WHITE_TXT   = "\x1b[1;37;40m"
    END_TXT     = "\x1b[0m"

    # add colors to logging text
    if level == 'success':
        logging_text = GREEN_TXT+logging_text+END_TXT
    elif level == 'info':
        logging_text = WHITE_TXT+logging_text+END_TXT
    elif level == 'warning':
        logging_text = YELLOW_TXT+logging_text+END_TXT
    elif level == 'error':
        logging_text = RED_TXT+logging_text+END_TXT            

    # Output logging text
    print(logging_text, flush=True)

# Tweaked: return result
import subprocess
def run_shell_script(script):
    logging("info", f"Runnning shell script: {script}")
    process = subprocess.run(script, shell = True, capture_output = True, encoding = 'utf-8')
    if process.returncode != 0:
        logging("error", f"shell script error: {process.stderr}")
        return(False)
    return(True)


def is_raspberrypi():
    '''
    Check if script is running on a raspberry pi.

    :return False - no, no RPI found
            True  - yes, a RPI found
    '''
    import os
    import io
 
    if os.name != 'posix': # portable operating system interface for unix
        return False
    status = False
    try:
        with io.open('/proc/cpuinfo', 'r') as cpuinfo:
            for line in cpuinfo:
                if line.startswith('Model'):
                    _, value = line.strip().split(':', 1)
                    value = value.strip()
                    if "Raspberry" in value:
                        status = True
                    else:
                        status = False
    except Exception:
        status = False
        pass
    return status


def get_sound_dictionairy_from_config(config):
    ''' 
    get the sound files which are defined in oradio_config.json
    and prepare a sound dictionary for easy look-up
    :param config = configuration settings in json
    :return sound_dictionary
    '''
    sys_sound_cfg         = config["system_sound"]
    sys_sound_directory   = sys_sound_cfg["system_sound_directory"]
    sound_dict = {}
    for item in sys_sound_cfg["sound"].items():
        sound_dict[item[0]] = sys_sound_directory+item[1]
    return(sound_dict)

