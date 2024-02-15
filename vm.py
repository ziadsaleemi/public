from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import ssl
from pyVim.task import WaitForTask

app = FastAPI()

class VMDeleteRequest(BaseModel):
    vcenter_server: str
    vm_name: str

class VMCreationRequest(BaseModel):
    vcenter_server: str
    datacenter_name: str
    cluster_name: str
    datastore_name: str
    template_name: str
    vm_name: str
    cpu: int
    memory: int  # In GB
    disk_size_gb: int
    network_name: str
    enable_cpu_hot_add: bool = False
    enable_memory_hot_add: bool = False

def get_ssl_context():
    context = ssl._create_unverified_context()
    return context

import json

def load_vcenter_creds_for_server(vcenter_server: str):
    try:
        with open('creds.json', 'r') as file:
            creds_list = json.load(file)
            for creds in creds_list:
                if creds['server'] == vcenter_server:
                    return creds
    except FileNotFoundError:
        print("The creds.json file was not found.")
    except json.JSONDecodeError:
        print("Error decoding JSON from creds.json.")
    
    return None
    

def get_obj(content, vimtype, name):
    """
    Get the vsphere object associated with a given text name.
    """
    obj = None
    container = content.viewManager.CreateContainerView(content.rootFolder, vimtype, True)
    for c in container.view:
        if c.name == name:
            obj = c
            break
    container.Destroy()
    return obj

def find_network(content, network_name, datacenter):
    """
    Find a network by name within a specific datacenter.
    """
    networks = datacenter.networkFolder.childEntity
    for network in networks:
        if isinstance(network, vim.Network) and network.name == network_name:
            return network
    return None

def create_vm_from_template(service_instance, vm_creation_request: VMCreationRequest):
    content = service_instance.RetrieveContent()

    # Objects have been found by get_obj and find_network functions
    datacenter = get_obj(content, [vim.Datacenter], vm_creation_request.datacenter_name)
    cluster = get_obj(content, [vim.ComputeResource], vm_creation_request.cluster_name)
    datastore = get_obj(content, [vim.Datastore], vm_creation_request.datastore_name)
    template_vm = get_obj(content, [vim.VirtualMachine], vm_creation_request.template_name)
    network = find_network(content, vm_creation_request.network_name, datacenter)

    # Create a clone specification
    clone_spec = vim.vm.CloneSpec()

    # Relocation spec
    reloc_spec = vim.vm.RelocateSpec()
    reloc_spec.datastore = datastore
    reloc_spec.pool = cluster.resourcePool
    clone_spec.location = reloc_spec

    # Configuration spec (for customizing CPU, memory, etc.)
    config_spec = vim.vm.ConfigSpec()
    config_spec.numCPUs = vm_creation_request.cpu
    config_spec.memoryMB = vm_creation_request.memory * 1024  # Convert GB to MB
    config_spec.cpuHotAddEnabled = vm_creation_request.enable_cpu_hot_add
    config_spec.memoryHotAddEnabled = vm_creation_request.enable_memory_hot_add
    clone_spec.config = config_spec

    # Network configuration
    nic_spec = vim.vm.device.VirtualDeviceSpec()
    nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
    nic_spec.device = vim.vm.device.VirtualVmxnet3()
    nic_spec.device.backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo()
    nic_spec.device.backing.network = network
    nic_spec.device.backing.deviceName = network.name
    nic_spec.device.connectable = vim.vm.device.VirtualDevice.ConnectInfo()
    nic_spec.device.connectable.startConnected = True

    # Add the network adapter to the clone spec
    device_change = [nic_spec]
    config_spec.deviceChange = device_change
    clone_spec.config = config_spec

    # Execute the clone task
    clone_task = template_vm.Clone(folder=datacenter.vmFolder, name=vm_creation_request.vm_name, spec=clone_spec)

    # Wait for the clone task to complete
    WaitForTask(clone_task)

    return {"vm_name": vm_creation_request.vm_name, "status": "VM creation completed"}

@app.post("/create-vm/")
async def create_vm_endpoint(vm_creation_request: VMCreationRequest):
    vcenter_creds = load_vcenter_creds_for_server(vm_creation_request.vcenter_server)
    ssl_context = get_ssl_context()
    try:
        service_instance = SmartConnect(host=vcenter_creds['server'], user=vcenter_creds['user'], pwd=vcenter_creds['password'], sslContext=ssl_context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to vCenter: {str(e)}")

    try:
        vm_creation_response = create_vm_from_template(service_instance, vm_creation_request)
    except Exception as e:
        Disconnect(service_instance)
        raise HTTPException(status_code=500, detail=f"VM creation failed: {str(e)}")

    Disconnect(service_instance)
    return vm_creation_response


def find_vm_by_name(service_instance, vm_name: str):
    content = service_instance.RetrieveContent()
    container = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
    for vm in container.view:
        if vm.name == vm_name:
            return vm
    return None

def delete_vm(service_instance, vm_name: str):
    vm = find_vm_by_name(service_instance, vm_name)
    if vm is None:
        return "VM not found"
    
    try:
        task = vm.Destroy_Task()
        WaitForTask(task)
        return "VM deleted successfully"
    except Exception as e:
        return f"Failed to delete VM: {str(e)}"

    
@app.post("/delete-vm/")
async def delete_vm_endpoint(request: VMDeleteRequest):
    # Load vCenter credentials (implement this function based on your setup)
    vcenter_creds = load_vcenter_creds_for_server(request.vcenter_server)
    if not vcenter_creds:
        raise HTTPException(status_code=404, detail="vCenter credentials not found")
    ssl_context = get_ssl_context()
    try:
        service_instance = SmartConnect(host=vcenter_creds['server'], user=vcenter_creds['user'], pwd=vcenter_creds['password'], sslContext=ssl_context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to vCenter: {str(e)}")

    # Attempt to delete the VM
    delete_status = delete_vm(service_instance, request.vm_name)

    Disconnect(service_instance)
    
    if delete_status != "VM deleted successfully":
        raise HTTPException(status_code=400, detail=delete_status)
    
    return {"detail": delete_status}
