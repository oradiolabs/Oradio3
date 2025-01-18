import inspect      # logging
import subprocess   # run_shell_script

# Simplified logging function - remove logging and monitoring, only print formatted log message
def logging(level, log_text):
    '''
    Simplified logging of log message
    :param level (str) - level of logging [ 'warning' | 'error' | 'info']
    :param log_text (str) - logging message
    '''
    # check whether rpi is throttled or running normal
    inspect_info = inspect.stack()
    module_info  = inspect.stack()[1]
    mod_name     = inspect.getmodule(module_info[0]).__name__
    frame_info   = inspect_info[1][0]
    func_name    = inspect.getframeinfo(frame_info)[2]

    # Build logging text
    logging_text = f'{mod_name:s} - {func_name:s} : {log_text:s}'

    RED_TXT     = "\x1b[31;20m"
    YELLOW_TXT  = "\033[93m"
    GREEN_TXT   = "\x1b[32m"
    END_TXT     = "\x1b[0m"

    # add colors to logging text
    if level == 'success':
        logging_text = GREEN_TXT+logging_text+END_TXT
    if level == 'warning':
        logging_text = YELLOW_TXT+logging_text+END_TXT
    if level == 'error':
        logging_text = RED_TXT+logging_text+END_TXT

    # Output logging text
    print(logging_text, flush=True)

# Tweaked: return result
def run_shell_script(script):
    logging("info", f"Runnning shell script: {script}")
    process = subprocess.run(script, shell = True, capture_output = True, encoding = 'utf-8')
    if process.returncode != 0:
        logging("error", f"shell script error: {process.stderr}")
        return False
    return True
