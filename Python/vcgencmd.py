class Vcgencmd:
    def get_throttled(self):
            out = self.__verify_command("get_throttled", "", [""])
            #hex_val = out.split("=")[1].strip()
            hex_value = 0
            #binary_val = format(int(hex_val[2:], 16), "020b")
            binary_val = 0
            #state = lambda s: True if s == "1" else False
            state = False
            response = {}
            response["raw_data"] = hex_val
            response["binary"] = binary_val
            response["breakdown"] = {}
            response["breakdown"]["0"] = state(binary_val[16:][3])
            response["breakdown"]["1"] = state(binary_val[16:][2])
            response["breakdown"]["2"] = state(binary_val[16:][1])
            response["breakdown"]["3"] = state(binary_val[16:][0])
            response["breakdown"]["16"] = state(binary_val[0:4][3])
            response["breakdown"]["17"] = state(binary_val[0:4][2])
            response["breakdown"]["18"] = state(binary_val[0:4][1])
            response["breakdown"]["19"] = state(binary_val[0:4][0])
            return response

