#
#  --------------------------------------------------------------------------
#   Gurux Ltd
#
#
#
#  Filename: $HeadURL$
#
#  Version: $Revision$,
#                   $Date$
#                   $Author$
#
#  Copyright (c) Gurux Ltd
#
# ---------------------------------------------------------------------------
#
#   DESCRIPTION
#
#  This file is a part of Gurux Device Framework.
#
#  Gurux Device Framework is Open Source software; you can redistribute it
#  and/or modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; version 2 of the License.
#  Gurux Device Framework is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#  See the GNU General Public License for more details.
#
#  More information of Gurux products: http://www.gurux.org
#
#  This code is licensed under the GNU General Public License v2.
#  Full text may be retrieved at http://www.gnu.org/licenses/gpl-2.0.txt
# ---------------------------------------------------------------------------
import datetime
from gurux_common.enums import TraceLevel
from gurux_common import ReceiveParameters
from gurux_dlms import GXByteBuffer, GXReplyData, GXDLMSTranslator, InterfaceType, GXDLMSException
from gurux_dlms.enums import ObjectType, Authentication, Conformance, DataType
from gurux_dlms.GXDLMSConverter import GXDLMSConverter
from gurux_dlms.objects import GXDLMSObject, GXDLMSRegister, GXDLMSDemandRegister, GXDLMSProfileGeneric
from gurux_net import GXNet

class GXDLMSReader:
    def __init__(self, client, media, trace):
        self.iec = False
        self.replyBuff = bytearray(8 + 1024)
        self.waitTime = 5000
        self.logFile = open("logFile.txt", "a")
        self.trace = trace
        self.media = media
        self.client = client
        if self.trace > TraceLevel.WARNING:
            print("Authentication: " + str(self.client.authentication))
            print("ClientAddress: " + hex(self.client.clientAddress))
            print("ServerAddress: " + hex(self.client.serverAddress))

    def close(self):
        #pylint: disable=broad-except
        if self.media and self.media.isOpen():
            print("DisconnectRequest")
            reply = GXReplyData()
            try:
                self.readDataBlock(self.client.releaseRequest(), reply)
            except Exception:
                pass
                #  All meters don't support release.
            reply.clear()
            self.readDLMSPacket(self.client.disconnectRequest(), reply)
            self.media.close()

    @classmethod
    def now(cls):        
        return datetime.datetime.now().strftime("%H:%M:%S")

    def writeTrace(self, line, level):
        if self.trace >= level:
            print(line)
        self.logFile.write(line)

    def readDLMSPacket(self, data, reply=None):
        if not reply:
            reply = GXReplyData()
        if isinstance(data, bytearray):
            self.readDLMSPacket2(data, reply)
        elif data:
            for it in data:
                reply.clear()
                self.readDLMSPacket2(it, reply)

    def readDLMSPacket2(self, data, reply):
        if not data:
            return
        notify = GXReplyData()
        reply.error = 0
        eop = 0x7E
        #In network connection terminator is not used.
        if self.client.interfaceType == InterfaceType.WRAPPER and isinstance(self.media, GXNet):
            eop = None
        p = ReceiveParameters()
        p.eop = eop
        p.waitTime = self.waitTime
        if eop is None:
            p.Count = 8
        else:
            p.Count = 5
        self.media.eop = eop
        rd = GXByteBuffer()
        with self.media.getSynchronous():
            if not reply.isStreaming():
                self.writeTrace("TX: " + self.now() + "\t" + GXByteBuffer.hex(data), TraceLevel.VERBOSE)
                self.media.send(data)
            pos = 0
            try:
                while not self.client.getData(rd, reply, notify):
                    if notify.data.size != 0:
                        if not notify.isMoreData():
                            t = GXDLMSTranslator()
                            xml = t.dataToXml(notify.data)
                            print(xml)
                            notify.clear()
                        continue
                    elif not p.eop:
                        p.count = self.client.getFrameSize(rd)
                    while not self.media.receive(p):
                        pos += 1
                        if pos == 3:
                            raise ValueError("Failed to receive reply from the device in given time.")
                        #If echo.
                        if not rd or len(rd) == len(data):
                            self.media.send(data, None)
                        #Try to read again...
                        print("Data send failed. Try to resend " + str(pos) + "/3")

                    rd.set(p.reply)
                    p.reply = None
                if pos == 3:
                    raise ValueError("Failed to receive reply from the device in given time.")
                if pos != 0:
                    print("Data send failed.  Try to resend " + str(pos) + "/3")
                pos += 1
            except Exception as e:
                self.writeTrace("RX: " + self.now() + "\t" + str(rd), TraceLevel.ERROR)
                raise e
            self.writeTrace("RX: " + self.now() + "\t" + str(rd), TraceLevel.VERBOSE)
            if reply.error != 0:
                raise GXDLMSException(reply.error)

    def readDataBlock(self, data, reply):
        if data:
            if isinstance(data, (list)):
                for it in data:
                    reply.clear()
                    self.readDataBlock(it, reply)
            else:
                self.readDLMSPacket(data, reply)
                while reply.isMoreData():
                    if reply.isStreaming():
                        data = None
                    else:
                        data = self.client.receiverReady(reply.moreData)
                    self.readDLMSPacket(data, reply)

    def initializeConnection(self):
        self.media.open()
        reply = GXReplyData()
        data = self.client.snrmRequest()
        if data:
            self.readDLMSPacket(data, reply)
            self.client.parseUAResponse(reply.data)
            size = self.client.limits.maxInfoTX + 40
            self.replyBuff = bytearray(size)
        reply.clear()
        self.readDataBlock(self.client.aarqRequest(), reply)
        self.client.parseAareResponse(reply.data)
        reply.clear()
        if self.client.authentication.value > Authentication.LOW.value:
            for it in self.client.getApplicationAssociationRequest():
                self.readDLMSPacket(it, reply)
            self.client.parseApplicationAssociationResponse(reply.data)

    def read(self, item, attributeIndex):
        data = self.client.read(item, attributeIndex)[0]
        reply = GXReplyData()
        self.readDataBlock(data, reply)
        if item.getDataType(attributeIndex) == DataType.NONE:
            item.setDataType(attributeIndex, reply.valueType)
        return self.client.updateValue(item, attributeIndex, reply.value)

    def readList(self, list_):
        if list_:
            data = self.client.readList(list_)
            reply = GXReplyData()
            values = list()
            for it in data:
                self.readDataBlock(it, reply)
                values.extend(reply.value)
                reply.clear()
            if len(values) != len(list_):
                raise ValueError("Invalid reply. Read items count do not match.")
            self.client.updateValues(list_, values)

    def write(self, item, attributeIndex):
        data = self.client.write(item, attributeIndex)
        self.readDLMSPacket(data)

    def GetColumns(self, pg):
        entries = self.read(pg, 7)
        print("Reading Profile Generic: " + pg.logicalName() + " " + pg.description() + " entries:" + str(entries))
        reply = GXReplyData()
        data = self.client.read(pg.name, pg.objectType, 3)[0]
        self.readDataBlock(data, reply)
        self.client.updateValue(pg, 3, reply.value)
        return pg.getCaptureObjects()

    def readRowsByEntry(self, pg, index, count):
        data = self.client.readRowsByEntry(pg, index, count)
        reply = GXReplyData()
        self.readDataBlock(data, reply)
        return self.client.updateValue(pg, 2, reply.value)

    def readRowsByRange(self, pg, start, end):
        reply = GXReplyData()
        data = self.client.readRowsByRange(pg, start, end)
        self.readDataBlock(data, reply)
        return self.client.updateValue(pg, 2, reply.value)

    def readScalerAndUnits(self):
        #pylint: disable=broad-except
        objs = self.client.objects.getObjects([ObjectType.REGISTER, ObjectType.EXTENDED_REGISTER, ObjectType.DEMAND_REGISTER])
        try:
            if self.client.negotiatedConformance & Conformance.MULTIPLE_REFERENCES != 0:
                list_ = list()
                for it in objs:
                    if isinstance(it, (GXDLMSRegister,)):
                        list_.append((it, 3))
                    elif isinstance(it, (GXDLMSDemandRegister,)):
                        list_.append((it, 4))
                self.readList(list_)
        except Exception:
            self.client.negotiatedConformance &= ~Conformance.MULTIPLE_REFERENCES
        if self.client.negotiatedConformance & Conformance.MULTIPLE_REFERENCES == 0:
            for it in objs:
                try:
                    if isinstance(it, (GXDLMSRegister,)):
                        self.read(it, 3)
                    elif isinstance(it, (GXDLMSDemandRegister,)):
                        self.read(it, 4)
                except Exception:
                    pass

    def getProfileGenericColumns(self):
        #pylint: disable=broad-except
        profileGenerics = self.client.objects.getObjects(ObjectType.PROFILE_GENERIC)
        for pg in profileGenerics:
            self.writeTrace("Profile Generic " + str(pg.name) + "Columns:", TraceLevel.INFO)
            try:
                self.read(pg, 3)
                if self.trace > TraceLevel.WARNING:
                    sb = ""
                    for k, v in pg.captureObjects:
                        if sb:
                            sb += " | "
                        sb += str(k.name)
                        sb += " "
                        desc = k.description
                        if desc:
                            sb += desc
                    self.writeTrace(sb, TraceLevel.INFO)
            except Exception as ex:
                self.writeTrace("Err! Failed to read columns:" + str(ex), TraceLevel.ERROR)

    def getReadOut(self):
        #pylint: disable=unidiomatic-typecheck, broad-except
        for it in self.client.objects:
            if type(it) == GXDLMSObject:
                print("Unknown Interface: " + it.objectType.__str__())
                continue
            if isinstance(it, GXDLMSProfileGeneric):
                continue

            self.writeTrace("-------- Reading " + str(it.objectType) + " " + str(it.name) + " " + it.description, TraceLevel.INFO)
            for pos in (it).getAttributeIndexToRead(True):
                try:
                    val = self.read(it, pos)
                    self.showValue(pos, val)
                except Exception as ex:
                    self.writeTrace("Error! Index: " + str(pos) + " " + str(ex), TraceLevel.ERROR)
                    self.writeTrace(str(ex), TraceLevel.ERROR)

    def showValue(self, pos, val):
        if isinstance(val, (bytes, bytearray)):
            val = GXByteBuffer(val)
        elif isinstance(val, list):
            str_ = ""
            for tmp in val:
                if str_:
                    str_ += ", "
                if isinstance(tmp, bytes):
                    str_ += GXByteBuffer.hex(tmp)
                else:
                    str_ += str(tmp)
            val = str_
        self.writeTrace("Index: " + str(pos) + " Value: " + str(val), TraceLevel.INFO)

    def getProfileGenerics(self):
        #pylint: disable=broad-except,too-many-nested-blocks
        cells = []
        profileGenerics = self.client.objects.getObjects(ObjectType.PROFILE_GENERIC)
        for it in profileGenerics:
            self.writeTrace("-------- Reading " + str(it.objectType) + " " + str(it.name) + " " + it.description, TraceLevel.INFO)
            entriesInUse = self.read(it, 7)
            entries = self.read(it, 8)
            self.writeTrace("Entries: " + str(entriesInUse) + "/" + str(entries), TraceLevel.INFO)
            pg = it
            if entriesInUse == 0 or not pg.captureObjects:
                continue
            try:
                cells = self.readRowsByEntry(pg, 1, 1)
                if self.trace > TraceLevel.WARNING:
                    for rows in cells:
                        for cell in rows:
                            if isinstance(cell, bytearray):
                                self.writeTrace(GXByteBuffer.hex(cell) + " | ", TraceLevel.INFO)
                            else:
                                self.writeTrace(str(cell) + " | ", TraceLevel.INFO)
                        self.writeTrace("", TraceLevel.INFO)
            except Exception as ex:
                self.writeTrace("Error! Failed to read first row: " + str(ex), TraceLevel.ERROR)
            try:
                start = datetime.datetime.now()
                end = start
                start.replace(hour=0, minute=0, second=0, microsecond=0)
                end.replace(minute=0, second=0, microsecond=0)
                cells = self.readRowsByRange(it, start, end)
                for rows in cells:
                    for cell in rows:
                        if isinstance(cell, bytearray):
                            print(GXByteBuffer.hexToBytes(cell) + " | ")
                        else:
                            self.writeTrace(str(cell) + " | ", TraceLevel.INFO)
                    self.writeTrace("", TraceLevel.INFO)
            except Exception as ex:
                self.writeTrace("Error! Failed to read last day: " + str(ex), TraceLevel.ERROR)

    def getAssociationView(self):
        reply = GXReplyData()
        self.readDataBlock(self.client.getObjectsRequest(), reply)
        objects = self.client.parseObjects(reply.data, True)
        converter = GXDLMSConverter()
        converter.updateOBISCodeInformation(objects)

    def readAll(self):
        try:
            self.initializeConnection()
            self.getAssociationView()
            self.readScalerAndUnits()
            self.getProfileGenericColumns()
            self.getReadOut()
            self.getProfileGenerics()
        finally:
            self.close()
