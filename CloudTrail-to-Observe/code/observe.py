import requests


class Observe:
    def __init__(self, customer_id, token, region="eu-1", extra=None):
        self.logs_endpoint = f"https://{customer_id}.collect.{region}.observeinc.com/v1/http"#?customer=ciso_prod&origin=python&logType=stale_resources"
        self.extra = extra
        self.token = token
        self.added_extra = False

    @staticmethod
    def get_name():
        return "Observe"

    def send_bulk(self, data, data_type="json"):
        headers = {
            "Content-Type": f"application/{data_type}",
            "Authorization": f"Bearer {self.token}"
        }
        if data_type == "json":
            if type(data) is list or type(data) is dict:
                if self.extra and len(self.extra) > 0 and not self.added_extra:
                    extra_array = self.extra.split(",")
                    self.logs_endpoint = self.logs_endpoint + "?"
                    endpoint_extra = ""
                    for item in extra_array:
                        k, v = item.split(":")
                        if len(endpoint_extra) == 0:
                            endpoint_extra += f"{k}={v}"
                        else:
                            endpoint_extra += f"&{k}={v}"
                    self.added_extra = True
                    self.logs_endpoint += endpoint_extra
                try:
                    # print(self.logs_endpoint)
                    requests.post(self.logs_endpoint, headers=headers, json=data)
                except Exception as e:
                    print(f"ERROR :: {e}")
            else:
                print("ERROR :: Unknown data received")
        else:
            print("ERROR :: Unknown data type")
