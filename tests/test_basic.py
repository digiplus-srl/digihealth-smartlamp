import pytest
from digihealth.sensors import SensorManager
from digihealth.processors import ProcessorManager
from digihealth.communicator import CommunicatorManager

def test_sensor_manager():
    manager = SensorManager()
    assert isinstance(manager.sensors, dict)

def test_processor_manager():
    manager = ProcessorManager()
    assert isinstance(manager.processors, list)

def test_communicator_manager():
    manager = CommunicatorManager()
    assert isinstance(manager.communicators, list)