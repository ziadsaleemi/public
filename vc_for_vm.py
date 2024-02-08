from fastapi import FastAPI, HTTPException
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import ssl
import json

app = FastAPI()

# Function to load vCenters from a JSON file
def load_vcenters_from_json(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)

# Path to the JSON file containing vCenter details
VCENTER_JSON_FILE = 'creds.json'

# Load vCenters into a variable
VCENTERS = load_vcenters_from_json(VCENTER_JSON_FILE)

# Function to create an SSL context that does not verify SSL certificates
def get_ssl_context():
    context = None
    if hasattr(ssl, '_create_unverified_context'):
        context = ssl._create_unverified_context()
    return context

# Function to search for a VM across multiple vCenters
def find_vm_across_vcenters(vm_name):
    for vcenter in VCENTERS:
        ssl_context = get_ssl_context()
        try:
            service_instance = SmartConnect(host=vcenter['server'], user=vcenter['user'], pwd=vcenter['password'], sslContext=ssl_context)
            content = service_instance.RetrieveContent()
            container = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
            vms = container.view
            for vm in vms:
                if vm.name == vm_name:
                    Disconnect(service_instance)
                    return vcenter['server']
            Disconnect(service_instance)
        except Exception as e:
            print(f"Error connecting to vCenter {vcenter['server']}: {e}")
    return None

@app.get("/find-vm/{vm_name}")
async def find_vm(vm_name: str):
    vcenter = find_vm_across_vcenters(vm_name)
    if vcenter:
        return {"vm_name": vm_name, "vcenter": vcenter}
    else:
        raise HTTPException(status_code=404, detail="VM not found across the specified vCenters")
