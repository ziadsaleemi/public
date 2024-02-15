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


####################
# Add network to VM

class NetworkAdditionRequest(BaseModel):
    vcenter_server: str
    vm_name: str
    network_name: str

def find_network(content, network_name):
    for datacenter in content.rootFolder.childEntity:
        if not hasattr(datacenter, 'networkFolder'):
            continue
        network_folder = datacenter.networkFolder
        networks = [net for net in network_folder.childEntity if net.name == network_name]
        if networks:
            return networks[0]  # Return the first matching network
    return None

def add_network_to_vm(service_instance, vm_name: str, network_name: str):
    content = service_instance.RetrieveContent()
    vm = find_vm_by_name(service_instance, vm_name)
    if vm is None:
        return "VM not found"

    network = find_network(content, network_name)
    if not network:
        return "Network not found"

    nic_spec = vim.vm.device.VirtualDeviceSpec()
    nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
    nic_spec.device = vim.vm.device.VirtualVmxnet3()
    nic_spec.device.backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo()
    nic_spec.device.backing.network = network
    nic_spec.device.backing.deviceName = network.name
    nic_spec.device.connectable = vim.vm.device.VirtualDevice.ConnectInfo()
    nic_spec.device.connectable.startConnected = True

    spec = vim.vm.ConfigSpec(deviceChange=[nic_spec])
    task = vm.ReconfigVM_Task(spec=spec)
    WaitForTask(task)
    return "Network adapter added successfully"

@app.post("/add-network-to-vm/")
async def add_network_to_vm_endpoint(request: NetworkAdditionRequest):
    vcenter_creds = load_vcenter_creds_for_server(request.vcenter_server)
    if not vcenter_creds:
        raise HTTPException(status_code=404, detail="vCenter credentials not found")
    ssl_context = get_ssl_context()
    try:
        service_instance = SmartConnect(host=vcenter_creds['server'], user=vcenter_creds['user'], pwd=vcenter_creds['password'],sslContext=ssl_context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to vCenter: {str(e)}")

    add_network_status = add_network_to_vm(service_instance, request.vm_name, request.network_name)

    Disconnect(service_instance)
    
    if add_network_status != "Network adapter added successfully":
        raise HTTPException(status_code=400, detail=add_network_status)
    
    return {"detail": add_network_status}



####################
# Remove network from VM
class NetworkRemovalRequest(BaseModel):
    vcenter_server: str
    vm_name: str
    network_label: str  # Optional: Use if you want to remove a specific network adapter by its label

def remove_network_from_vm(service_instance, vm_name: str, network_label: str):
    content = service_instance.RetrieveContent()
    vm = find_vm_by_name(service_instance, vm_name)
    if not vm:
        return "VM not found"

    # Find the network adapter to remove
    for device in vm.config.hardware.device:
        print(device.deviceInfo)
        if isinstance(device, vim.vm.device.VirtualEthernetCard) and device.deviceInfo.label == network_label:
            nic_key = device.key
            break
    else:
        return "Network adapter not found"

    # Create a device spec to remove the network adapter
    virtual_device_spec = vim.vm.device.VirtualDeviceSpec()
    virtual_device_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.remove
    virtual_device_spec.device = vim.vm.device.VirtualEthernetCard(key=nic_key)

    # Create a VM config spec and assign the device change
    config_spec = vim.vm.ConfigSpec(deviceChange=[virtual_device_spec])

    # Reconfigure the VM
    task = vm.ReconfigVM_Task(spec=config_spec)
    WaitForTask(task)
    return "Network adapter removed successfully"


@app.post("/remove-network-from-vm/")
async def remove_network_from_vm_endpoint(request: NetworkRemovalRequest):
    vcenter_creds = load_vcenter_creds_for_server(request.vcenter_server)
    if not vcenter_creds:
        raise HTTPException(status_code=404, detail="vCenter credentials not found")
    ssl_context = get_ssl_context()
    try:
        service_instance = SmartConnect(host=vcenter_creds['server'], user=vcenter_creds['user'], pwd=vcenter_creds['password'],sslContext=ssl_context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to vCenter: {str(e)}")

    remove_network_status = remove_network_from_vm(service_instance, request.vm_name, request.network_label)

    Disconnect(service_instance)
    
    if remove_network_status != "Network adapter removed successfully":
        raise HTTPException(status_code=400, detail=remove_network_status)
    
    return {"detail": remove_network_status}



########
# Add disk to VM
class DiskAdditionRequest(BaseModel):
    vcenter_server: str
    vm_name: str
    disk_size_gb: int
    datastore_name: str

def find_datastore(service_instance, datastore_name):
    content = service_instance.RetrieveContent()
    for datacenter in content.rootFolder.childEntity:
        if hasattr(datacenter, 'datastoreFolder'):
            datastore_folder = datacenter.datastoreFolder
            for datastore in datastore_folder.childEntity:
                if datastore.name == datastore_name:
                    return datastore
    return None


def add_disk_to_vm(service_instance, vm_name: str, disk_size_gb: int, datastore_name: str):
    content = service_instance.RetrieveContent()
    vm = find_vm_by_name(service_instance, vm_name)
    if not vm:
        return "VM not found"

    datastore = find_datastore(service_instance, datastore_name)
    if not datastore:
        return "Datastore not found"

    # Create a new virtual disk
    spec = vim.vm.ConfigSpec()
    unit_number = 0
    for dev in vm.config.hardware.device:
        if hasattr(dev.backing, 'fileName'):
            unit_number = max(unit_number, int(dev.unitNumber) + 1)
            if unit_number == 7:  # SCSI controller reserved
                unit_number += 1

    dev_changes = []
    new_disk_kb = disk_size_gb * 1024 * 1024
    disk_spec = vim.vm.device.VirtualDeviceSpec()
    disk_spec.fileOperation = "create"
    disk_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
    disk_spec.device = vim.vm.device.VirtualDisk()
    disk_spec.device.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
    disk_spec.device.backing.diskMode = 'persistent'
    disk_spec.device.backing.datastore = datastore
    disk_spec.device.unitNumber = unit_number
    disk_spec.device.capacityInKB = new_disk_kb
    disk_spec.device.controllerKey = 1000  # Typically SCSI controller key
    dev_changes.append(disk_spec)
    spec.deviceChange = dev_changes

    # Add the disk to the VM
    task = vm.ReconfigVM_Task(spec=spec)
    WaitForTask(task)
    return "Disk added successfully"

@app.post("/add-disk-to-vm/")
async def add_disk_to_vm_endpoint(request: DiskAdditionRequest):
    vcenter_creds = load_vcenter_creds_for_server(request.vcenter_server)
    if not vcenter_creds:
        raise HTTPException(status_code=404, detail="vCenter credentials not found")
    ssl_context = get_ssl_context()
    try:
        service_instance = SmartConnect(host=vcenter_creds['server'], user=vcenter_creds['user'], pwd=vcenter_creds['password'],sslContext=ssl_context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to vCenter: {str(e)}")

    add_disk_status = add_disk_to_vm(service_instance, request.vm_name, request.disk_size_gb, request.datastore_name)

    Disconnect(service_instance)
    
    if add_disk_status != "Disk added successfully":
        raise HTTPException(status_code=400, detail=add_disk_status)
    
    return {"detail": add_disk_status}


########
# Remove disk from VM
class DiskRemovalRequest(BaseModel):
    vcenter_server: str
    vm_name: str
    disk_label: str

def remove_disk_from_vm(service_instance, vm_name: str, disk_label: str):
    content = service_instance.RetrieveContent()
    vm = find_vm_by_name(service_instance, vm_name)
    if not vm:
        return "VM not found"

    # Find the disk to remove
    for device in vm.config.hardware.device:
        if isinstance(device, vim.vm.device.VirtualDisk) and device.deviceInfo.label == disk_label:
            disk_key = device.key
            break
    else:
        return "Disk not found"

    # Create a device spec to remove the disk
    virtual_device_spec = vim.vm.device.VirtualDeviceSpec()
    virtual_device_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.remove
    virtual_device_spec.device = vim.vm.device.VirtualDisk(key=disk_key)

    # Create a VM config spec and assign the device change
    config_spec = vim.vm.ConfigSpec(deviceChange=[virtual_device_spec])

    # Reconfigure the VM
    task = vm.ReconfigVM_Task(spec=config_spec)
    WaitForTask(task)
    return "Disk removed successfully"

@app.post("/remove-disk-from-vm/")
async def remove_disk_from_vm_endpoint(request: DiskRemovalRequest):
    vcenter_creds = load_vcenter_creds_for_server(request.vcenter_server)
    if not vcenter_creds:
        raise HTTPException(status_code=404, detail="vCenter credentials not found")
    ssl_context = get_ssl_context()
    try:
        service_instance = SmartConnect(host=vcenter_creds['server'], user=vcenter_creds['user'], pwd=vcenter_creds['password'],sslContext=ssl_context)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to vCenter: {str(e)}")

    remove_disk_status = remove_disk_from_vm(service_instance, request.vm_name, request.disk_label)

    Disconnect(service_instance)
    
    if remove_disk_status != "Disk removed successfully":
        raise HTTPException(status_code=400, detail=remove_disk_status)
    
    return {"detail": remove_disk_status}