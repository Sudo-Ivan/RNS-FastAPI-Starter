import os, time
from fastapi import FastAPI, HTTPException, WebSocket
import RNS
from LXMF import LXMRouter, LXMessage
from queue import Queue
from types import SimpleNamespace
from pydantic import BaseModel
import threading
import asyncio

class MessageRequest(BaseModel):
    destination: str
    message: str
    title: str = "Reply"

class LXMFAPI:
    delivery_callbacks = []
    receipts = []
    queue = Queue(maxsize = 5)
    announce_time = 600

    def __init__(self, name='LXMFAPI', announce=600, announce_immediately=False):
        self.config_path = os.path.join(os.getcwd(), 'config')
        if not os.path.isdir(self.config_path):
            os.mkdir(self.config_path)
        idfile = os.path.join(self.config_path, "identity")
        if not os.path.isfile(idfile):
            RNS.log('No Primary Identity file found, creating new...', RNS.LOG_INFO)
            id = RNS.Identity(True)
            id.to_file(idfile)
        self.id = RNS.Identity.from_file(idfile)
        RNS.log('Loaded identity from file', RNS.LOG_INFO)
        if announce_immediately:
            af = os.path.join(self.config_path, "announce")
            if os.path.isfile(af):
                os.remove(af)
                RNS.log('Announcing now. Timer reset.', RNS.LOG_INFO)
        RNS.Reticulum(loglevel=RNS.LOG_VERBOSE)
        self.router = LXMRouter(identity = self.id, storagepath = self.config_path)
        self.local = self.router.register_delivery_identity(self.id, display_name=name)
        self.router.register_delivery_callback(self._message_received)
        RNS.log('LXMF Router ready to receive on: {}'.format(RNS.prettyhexrep(self.local.hash)), RNS.LOG_INFO)
        self._announce()
        self._start_queue_processor()

    def _start_queue_processor(self):
        def process_queue():
            while True:
                if not self.queue.empty():
                    lxm = self.queue.get()
                    self.router.handle_outbound(lxm)
                self._announce()
                time.sleep(10)
        
        thread = threading.Thread(target=process_queue, daemon=True)
        thread.start()

    def _announce(self):
        announce_path = os.path.join(self.config_path, "announce")
        if os.path.isfile(announce_path):
            with open(announce_path, "r") as f:
                announce = int(f.readline())
        else:
            announce = 1
        if announce > int(time.time()):
            RNS.log('Recent announcement', RNS.LOG_DEBUG)
        else:
            with open(announce_path, "w+") as af:
                next_announce = int(time.time()) + self.announce_time
                af.write(str(next_announce))
            self.local.announce()
            RNS.log('Announcement sent, expr set 1800 seconds', RNS.LOG_INFO)

    def received(self, function):
        self.delivery_callbacks.append(function)
        return function

    def _message_received(self, message):
        sender = RNS.hexrep(message.source_hash, delimit=False)
        receipt = RNS.hexrep(message.hash, delimit=False)
        RNS.log(f'Message receipt <{receipt}>', RNS.LOG_INFO)
        def reply(msg):
            self.send(sender, msg)
        if receipt not in self.receipts:
            self.receipts.append(receipt)
            if len(self.receipts) > 100:
                self.receipts.pop(0)
            for callback in self.delivery_callbacks:
                obj = {
                    'lxmf' : message,
                    'reply' : reply,
                    'sender' : sender,
                    'content' : message.content.decode('utf-8'),
                    'hash' : receipt
                }
                msg = SimpleNamespace(**obj)
                callback(msg)

    def send(self, destination, message, title='Reply'):
        try:
            hash = bytes.fromhex(destination)
        except Exception as e:
            RNS.log("Invalid destination hash", RNS.LOG_ERROR)
            return
        if not len(hash) == RNS.Reticulum.TRUNCATED_HASHLENGTH//8:
            RNS.log("Invalid destination hash length", RNS.LOG_ERROR)
        else:
            id = RNS.Identity.recall(hash)
            if id == None:
                RNS.log("Could not recall an Identity for the requested address. You have probably never received an announce from it. Try requesting a path from the network first. In fact, let's do this now :)", RNS.LOG_ERROR)
                RNS.Transport.request_path(hash)
                RNS.log("OK, a path was requested. If the network knows a path, you will receive an announce with the Identity data shortly.", RNS.LOG_INFO)
            else:
                lxmf_destination = RNS.Destination(id, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
                lxm = LXMessage(lxmf_destination, self.local, message, title=title, desired_method=LXMessage.DIRECT)
                lxm.try_propagation_on_fail = True
                self.queue.put(lxm)

app = FastAPI()
api = LXMFAPI()

@app.post("/send")
async def send_message(message_request: MessageRequest):
    try:
        api.send(
            message_request.destination,
            message_request.message,
            message_request.title
        )
        return {"status": "message queued"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/status")
async def get_status():
    return {
        "queue_size": api.queue.qsize(),
        "receipts": len(api.receipts),
        "address": RNS.prettyhexrep(api.local.hash)
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket client connected")
    
    message_queue = asyncio.Queue()
    loop = asyncio.get_event_loop()
    
    def message_callback(msg):
        print(f"Received LXMF message, queueing for websocket: {msg.content}")
        loop.call_soon_threadsafe(
            lambda: asyncio.run_coroutine_threadsafe(
                message_queue.put({
                    "sender": msg.sender,
                    "content": msg.content,
                    "hash": msg.hash
                }), 
                loop
            )
        )
    
    api.received(message_callback)
    
    try:
        while True:
            try:
                message = await asyncio.wait_for(message_queue.get(), timeout=1.0)
                print(f"Sending message to websocket: {message}")
                await websocket.send_json(message)
            except asyncio.TimeoutError:
                # No message in queue, continue waiting
                continue
            except Exception as e:
                print(f"Error in websocket loop: {e}")
                break
    except Exception as e:
        print(f"WebSocket connection closed: {e}")
    finally:
        api.delivery_callbacks.remove(message_callback)
        print("WebSocket client disconnected")