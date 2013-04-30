''' papylib's worker - an emesene extension for papyon library '''
# -*- coding: utf-8 -*-
#
# papylib - an emesene extension for papyon
#
# Copyright (C) 2009-2010 Riccardo (C10uD) <c10ud.dev@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import os
import sys
import time
import Queue
import glib
import hashlib
import tempfile
import xml.sax.saxutils

import e3
from e3 import cache
from e3.base import *
import e3.base.Logger as Logger
from e3.common import ConfigDir
from e3.common import locations

import logging
log = logging.getLogger('papylib.Worker')

papypath = os.path.abspath("e3" + os.sep + "papylib" + os.sep + "papyon")
if os.path.exists(papypath):
    sys.path.insert(0, papypath)

try:
    REQ_VER = (0, 5, 4)

    import papyon
    import papyon.event
    import papyon.service.ContentRoaming as CR
    import papyon.util.string_io as StringIO

    papyver = papyon.version
    if papyver[1] == REQ_VER[1]:
        if papyver[2] < REQ_VER[2]:
            raise Exception
    elif papyver[1] < REQ_VER[1]:
        raise Exception
except ImportError, ie:
    print ie
except Exception, e:
    log.exception("python-papyon(>=%s.%s.%s) required "
        "in order to use this extension" % REQ_VER)

from PapyEvents import *
from PapyConvert import *

PAPY_HAS_AUDIOVIDEO = False

# We don't currently support audio/video conversations
# because the required codecs aren't available in gstreamer
#try:
#    import PapyConference
#    import papyon.media.constants
#    PAPY_HAS_AUDIOVIDEO = True
#except ImportError, e:
#    log.exception("You need gstreamer to use the Audio/Video calls support")

class Worker(e3.base.Worker, papyon.Client):
    ''' papylib's worker - an emesene extension for papyon library '''

    def __init__(self, session, proxy, use_http=False, use_ipv6=False):
        '''class constructor'''
        e3.base.Worker.__init__(self, session)
        server = ('messenger.hotmail.com', 1863)

        if use_http:
            from papyon.transport import HTTPPollConnection
            self.client = papyon.Client.__init__(self, server, get_proxies(),
                HTTPPollConnection, version=18)
        else:
            self.client = papyon.Client.__init__(self, server,
                proxies=get_proxies(), version=18)

        self.profile_url = PROFILE_URL

        # set machine guid
        if session.account_uuid is not None:
            self.machine_guid = session.account_uuid

        self._event_handler = ClientEvents(self)
        self._contact_handler = ContactEvent(self)
        self._invite_handler = InviteEvent(self)
        self._abook_handler = AddressBookEvent(self)
        self._profile_handler = ProfileEvent(self)
        self._oim_handler = OfflineEvent(self)
        self._mail_handler = MailboxEvent(self)

        # this stores account : cid
        self.conversations = {}
        # this stores cid : account
        self.rconversations = {}
        # this stores papyon conversations as cid : conversation
        self.papyconv = {}
        # this stores papyon conversations as conversation : cid
        self.rpapyconv = {}
        # this stores conversation handlers
        self._conversation_handler = {}
        # store ongoing filetransfers
        self.filetransfers = {}
        self.rfiletransfers = {}
        # store ongoing calls
        self.calls = {}
        self.rcalls = {}

    # some useful methods (mostly, gui only)
    def set_initial_infos(self):
        '''this is called on login'''
        self.content_roaming.connect("notify::state",
            self._content_roaming_state_changed)
        self.content_roaming.sync()
        # sets the login-chosen presence in papyon
        presence = self.session.account.status
        self.session.contacts.me.picture = self.session.config_dir.get_path(
            "last_avatar")
        self._set_status(presence)

        # Set a bunch of client's capabilites
        self.profile.client_capabilities.renders_gif = False
        self.profile.client_capabilities.renders_isf = False
        self.profile.client_capabilities.supports_activities = False
        self.profile.client_capabilities.supports_winks = False
        self.profile.client_capabilities.supports_shared_search = False
        self.profile.client_capabilities.supports_voice_over_msnp = False
        self.profile.client_capabilities.supports_uucp_sip_stack = False
        self.profile.client_capabilities.supports_sharing_video = False
        self.profile.client_capabilities.circle_voiceim_enabled = False
        self.profile.client_capabilities.sharing_enabled = False
        self.profile.client_capabilities.mobile_suspendim_fanout_disable = False
        self.profile.client_capabilities.supports_videocall_16x9 = False
        self.profile.client_capabilities.siptunnel_v2 = False
        self.profile.client_capabilities.supports_wma_voiceclip = False
        self.profile.client_capabilities.supports_circleim_voiceclip = False
        self.profile.client_capabilities.supports_socialnews_objecttypes = False
        global PAPY_HAS_AUDIOVIDEO
        self.profile.client_capabilities.supports_voice_im = PAPY_HAS_AUDIOVIDEO
        self.profile.client_capabilities.supports_sip_invite = PAPY_HAS_AUDIOVIDEO
        self.profile.client_capabilities.supports_tunneled_sip = PAPY_HAS_AUDIOVIDEO
        self.profile.client_capabilities.supports_shared_drive = PAPY_HAS_AUDIOVIDEO
        self.profile.client_capabilities.supports_rtc_video = PAPY_HAS_AUDIOVIDEO
        self.profile.client_capabilities.has_webcam = PAPY_HAS_AUDIOVIDEO

        self.profile.privacy = papyon.profile.Privacy.BLOCK

        self.my_avatars = self.session.caches.get_avatar_cache(
                self.session.account.account)

    def _content_roaming_state_changed(self, cr, pspec):
        if cr.state == CR.constants.ContentRoamingState.SYNCHRONIZED:
            picfail = False

            try:
                type, data = cr.display_picture
                handle, path = tempfile.mkstemp(
                    suffix="." + type.split('/')[1], prefix='emsnpic')
                os.close(handle)
                f = open(path, 'wb')
                f.write(data)
                f.close()
            except Exception as e:
                picfail = True
                log.error("Writing of content roaming picture failed: %s" % e)

            # update roaming stuff in papyon's session changing display_name
            # doesn't seem to update its value istantly, wtf?  however, other
            # clients see this correctly, wow m3n
            self.profile.display_name = str(cr.display_name)

            if cr.personal_message is not None:
                self.profile.personal_message = str(cr.personal_message)

            self.session.profile_get_succeed(str(cr.display_name),
                    self.profile.personal_message)

            if not picfail:
                self._handle_action_set_picture(path, True)

    def _set_status(self, stat):
        ''' changes the presence in papyon given an e3 status '''
        self.session.contacts.me.status = stat
        self.profile.presence = STATUS_E3_TO_PAPY[stat]

    def _fill_contact_list(self, abook):
        ''' fill the contact list with papy contacts '''
        for group in abook.groups:
            self._add_group(group)

        for contact in abook.contacts:
            if (papyon.profile.Membership.PENDING & contact.memberships):
                # Add to the pending contacts
                tmp_cont = e3.base.Contact(contact.account, contact.id,
                    contact.display_name, contact.personal_message,
                    STATUS_PAPY_TO_E3[contact.presence],
                    contact.display_name,
                    (papyon.profile.Membership.BLOCK & contact.memberships))

                self.session.contacts.pending[contact.account] = tmp_cont
                continue

            if not (papyon.profile.Membership.FORWARD & contact.memberships):
                # This skips contacts that are not in our contact list
                # but are still in the Live Address Book
                continue

            self._add_contact(contact)

            for group in contact.groups:
                self._add_contact_to_group(contact, group)

        self.session.contact_list_ready()

    def _add_contact(self, papycontact):
        ''' helper method to add a contact to the (gui) contact list '''
        contact = e3.base.Contact(papycontact.account, papycontact.id,
            papycontact.display_name, papycontact.personal_message,
            STATUS_PAPY_TO_E3[papycontact.presence], papycontact.alias,
            (papyon.profile.Membership.BLOCK & papycontact.memberships))

        self.session.contacts.contacts[papycontact.account] = contact

        avatars = self.session.caches.get_avatar_cache(papycontact.account)

        if 'last' in avatars:
            contact.picture = os.path.join(avatars.path, 'last')

        glib.timeout_add_seconds(5, lambda *arg:
            self._lazy_msnobj_checker(papycontact))

    def _lazy_msnobj_checker(self, papycontact):
        ''' ugly hack because of https://github.com/emesene/emesene/issues/602
            aka papyon doesn't tell us msnobjects changed (and if we check
            too soon, they're None) '''
        self._on_contact_msnobject_changed(papycontact)
        return False

    def _add_group(self, papygroup):
        ''' method to add a group to the (gui) contact list '''
        gid = papygroup.id
        self.session.groups[gid] = e3.base.Group(papygroup.name, gid)

    def _add_contact_to_group(self, papycontact, papygroup):
        ''' method to add a contact to a (gui) group '''
        self.session.groups[papygroup.id].contacts.append(papycontact.account)
        contact = self.session.contacts.contacts[papycontact.account]
        contact.groups.append(papygroup.id)

    def _remove_contact_from_group(self, papycontact, papygroup):
        ''' removes a contact from a group (gui) '''
        contacts = self.session.groups[papygroup.id].contacts

        if papycontact.account in contacts:
            contacts.remove(papycontact.account)

        contacts1 = self.session.contacts.contacts[papycontact.account]

        if papygroup.id in contacts1.groups:
            contacts1.groups.remove(papygroup.id)

    def _rename_group(self, papygroup):
        ''' renames a group (gui) '''
        self.session.groups[papygroup.id] = e3.Group(papygroup.name,
            papygroup.id)

    def _block_contact(self, papycontact):
        ''' blocks a contact (gui) '''
        self.session.contacts.contacts[papycontact.account].blocked = True

    def _unblock_contact(self, papycontact):
        ''' unblocks a contact (gui) '''
        self.session.contacts.contacts[papycontact.account].blocked = False

    # invite handlers
    def _on_conversation_invite(self, papyconversation):
        ''' called when we are invited in a conversation '''
        cid = time.time()
        participants = list(papyconversation.participants)
        members = [account.account for account in participants]

        conv_id = members[0] if len(participants) == 1 else 'GroupChat'+str(cid)

        self.conversations[conv_id] = cid
        self.rconversations[cid] = conv_id
        newconversationevent = ConversationEvent(papyconversation, self)
        self._conversation_handler[cid] = newconversationevent
        self.papyconv[cid] = papyconversation
        self.rpapyconv[papyconversation] = cid
        self.session.conv_first_action(cid, members)

    def _on_conference_invite(self, call):
        '''handles a conference (call) invite'''
        return # skip it, we don't support it
        account = call.peer.account
        if account in self.conversations:
            cid = self.conversations[account]
        else:
            cid = time.time()
            self._handle_action_new_conversation(account, cid)
            self.session.conv_first_action(cid, [account])

        ca = e3.base.Call(call, call.peer.account)
        self.calls[call] = ca
        self.rcalls[ca] = call
        call_handler = CallEvent(call, self)

        call.ring() #Hello, we're waiting for user input

        self.session.call_invitation(ca, cid, False)

    def contact_by_account(self, account):
        '''return a papyon contact from the account'''
        contacts = self.address_book.contacts.search_by('account', account)

        if len(contacts) == 0:
            log.error("contact %s not found on address book" % account)
            return None
        else:
            return contacts[0]

    def _on_invite_file_transfer(self, papysession):
        ''' handle file transfer invites '''

        account = papysession.peer.account
        papycontact = self.contact_by_account(account)

        if account in self.conversations:
            cid = self.conversations[account]
        else:
            cid = time.time()
            self._handle_action_new_conversation(account, cid)
            self.session.conv_first_action(cid, [account])

        tr = e3.base.FileTransfer(papysession, papysession.filename,
                papycontact, papysession.size, papysession.preview,
                sender=papysession.peer)

        self.filetransfers[papysession] = tr
        self.rfiletransfers[tr] = papysession

        papysession.connect("accepted", self.papy_ft_accepted)
        papysession.connect("progressed", self.papy_ft_progressed)
        papysession.connect("completed", self.papy_ft_completed)
        papysession.connect("rejected", self.papy_ft_rejected)
        papysession.connect("canceled", self.papy_ft_canceled)

        self.session.filetransfer_invitation(tr, cid)

    def papy_ft_canceled(self, ftsession):
        tr = self.filetransfers[ftsession]
        self.session.filetransfer_canceled(tr)

    def papy_ft_accepted(self, ftsession):
        tr = self.filetransfers[ftsession]
        filedata = self._filedata_to_string(tr.completepath)
        ftsession.send(StringIO.StringIO(filedata))

        self.session.filetransfer_accepted(tr)

    def papy_ft_progressed(self, ftsession, len_chunk):
        tr = self.filetransfers[ftsession]
        tr.received_data += len_chunk

        self.session.filetransfer_progress(tr)

    def papy_ft_completed(self, ftsession, data):
        ''' save the file according to user prefs
            or do nothing if we sent it '''
        # TODO: kill the dicts (?)
        tr = self.filetransfers[ftsession]
        if tr.sender == 'Me':
            # we sent the file, do nothing pls.
            pass
        else:
            download_path = self.session.config.get_or_set("download_folder",
                e3.common.locations.downloads())
            if self.session.config.b_download_folder_per_account:
                full_path = os.path.join(download_path, tr.sender.account,
                    tr.filename)
                file_dir = os.path.join(download_path, tr.sender.account)
                if not os.path.isdir(file_dir):
                    os.mkdir(file_dir)
            else:
                full_path = os.path.join(download_path, tr.filename)
            try:
                f = open(full_path, 'wb')
                f.write(data.getvalue())
                f.close()
                tr.completepath = full_path
            except Exception as e:
                log.error("Writing file %s failed: %s" % (full_path, e))
        #del self.rfiletransfers[tr]

        self.session.filetransfer_completed(tr)

    def papy_ft_rejected(self, ftsession):
        tr = self.filetransfers[ftsession]

        self.session.filetransfer_rejected(tr)

    # call handlers
    def _on_call_incoming(self, papycallevent):
        """Called once the incoming call is ready."""
        log.info("New call incoming")
        #if papycallevent._call.media_session.prepared:
        #    papycallevent._call.accept()
        #    log.info("Accepting the new call")
        #else:
        #    papycallevent._call.ring()
        #    log.info("Ringing...session not ready")

    def _on_call_ringing(self, papycallevent):
        log.info("Ringing")

    def _on_call_accepted(self, papycallevent):
        log.info("Call accepted")

    def _on_call_rejected(self, papycallevent, response):
        log.info("Call rejected: %s" % response)

    def _on_call_error(self, papycallevent, response):
        log.error("Call error: %s" % response)

    def _on_call_missed(self, papycallevent):
        log.info("Call missed")

    def _on_call_connected(self, papycallevent):
        log.info("Call connected")

    def _on_call_ended(self, papycallevent):
        log.info("Call ended")

    # conversation handlers
    def _on_conversation_oim_received(self, flnmsg):
        ''' handle offline messages '''
        account = flnmsg.sender.account
        msg = str(flnmsg)

        if account in self.conversations:
            cid = self.conversations[account]
        else:
            cid = time.time()
            self._handle_action_new_conversation(account, cid)
            self.session.conv_first_action(cid,
                [account])

        msgobj = e3.base.Message(e3.base.Message.TYPE_FLNMSG,
            msg, account, None, flnmsg.date, flnmsg.sender.display_name)
        # override font size!
        msgobj.style.size = self.session.config.i_font_size
        self.session.conv_message(cid, account, msgobj, {})
        e3.Logger.log_message(self.session, None, msgobj, False)

    def _on_conversation_user_typing(self, papycontact, pyconvevent):
        ''' handle user typing event '''
        account = papycontact.account
        conv = pyconvevent.conversation

        if conv in self.rpapyconv:
            cid = self.rpapyconv[conv]
        else:
            # we don't care about users typing if no conversation is opened
            return

        self.session.user_typing(cid, account)

    def _on_conversation_message_received(self, papycontact, papymessage,
        pyconvevent):
        ''' handle the reception of a message '''
        account = papycontact.account
        conv = pyconvevent.conversation
        if conv in self.rpapyconv:
            cid = self.rpapyconv[conv]
        else:
            raise Exception("CID Error", "Not in rpapyconv - did not receive an invite for this message")
        '''
        else: # TODO:FIXME: this never happens (?)
            # emesene must create another conversation
            cid = time.time()
            self.conversations[account] = cid # add to account:cid
            self.rconversations[cid] = account
            self._conversation_handler[cid] = pyconvevent # add conv handler
            self.papyconv[cid] = pyconvevent.conversation # add papy conv
            self.rpapyconv[pyconvevent.conversation] = cid
            self.session.conv_first_action(cid, [account])
        '''
        msgobj = e3.base.Message(e3.base.Message.TYPE_MESSAGE,
                papymessage.content, account,
                formatting_papy_to_e3(papymessage.formatting,
                    self.session.config.i_font_size),
                 #support p4-context nams
                display_name=papymessage.display_name)

        # convert papyon msnobjects to a simple dict {shortcut:identifier}
        received_custom_emoticons = {}

        emotes = self.session.caches.get_emoticon_cache(account)

        def download_failed(reason):
            log.error("Custom emoticon download failed: %s" % reason)

        def download_ok(msnobj, download_failed_func, received, filename, short):
            if msnobj._data is None:
                log.warning("downloaded msnobj is None")
                return

            shortcut, hash_ = emotes.insert_resized((short, msnobj._data),
                                                    filename)

            path = os.path.join(emotes.path, hash_)
            received[short] = path
            self.session.p2p_finished(account, 'emoticon', msnobj._creator,
                                      shortcut, path)

        for shortcut, msn_object in papymessage.msn_objects.iteritems():
            if shortcut in received_custom_emoticons:
                break # avoid multi p2p

            emoticon_hash = msn_object._data_sha.encode("hex")
            emoticon_path = os.path.join(emotes.path, emoticon_hash)
            received_custom_emoticons[shortcut] = emoticon_path

            if not emotes.has_emote(shortcut):
                self.msn_object_store.request(msn_object,
                    (download_ok, download_failed,
                     received_custom_emoticons, emoticon_hash, shortcut), peer=papycontact)

        self.session.conv_message(cid, account, msgobj,
                received_custom_emoticons)

        #XXX: when we send messages from the web iface we get those here, so log them propertly
        if msgobj.account == self.session.contacts.me.account:
            participants = [x.account for x in conv.participants]
            sent = True
        else:
            participants = None
            sent = False

        e3.Logger.log_message(self.session, participants, msgobj, sent, cid = cid)

    def _on_conversation_nudge_received(self, papycontact, pyconvevent):
        ''' handle received nudges '''
        account = papycontact.account
        conv = pyconvevent.conversation

        if conv in self.rpapyconv:
            cid = self.rpapyconv[conv]
        else:
            #print "must create another conversation"
            cid = time.time()
            self.conversations[account] = cid # add to account:cid
            self.rconversations[cid] = account
            self._conversation_handler[cid] = pyconvevent # add conv handler
            self.papyconv[cid] = pyconvevent.conversation # add papy conv
            self.rpapyconv[pyconvevent.conversation] = cid
            self.session.conv_first_action(cid, [account])

        msgobj = e3.base.Message(e3.base.Message.TYPE_NUDGE, None,
            account, None, None, papycontact.display_name)

        self.session.conv_message(cid, account, msgobj)
        e3.Logger.log_message(self.session, None, msgobj, False)

    def _on_conversation_message_error(self, err_type, error, convevent):
        if err_type != papyon.event.ConversationErrorType.MESSAGE:
            return #discard silently

        cid = self.rpapyconv[convevent.conversation]

        msgobj = e3.base.Message(e3.base.Message.TYPE_MESSAGE, error,
                self.session.account, None)
        self.session.conv_message_send_failed(cid, msgobj)

        log.error("Error sending message: %s %s" % (err_type, error))

    def _on_conversation_user_joined(self, papycontact, pyconvevent):
        '''handle user joined event'''
        account = papycontact.account
        conv = pyconvevent.conversation

        if len(conv.total_participants) == 1:
            return
        else:
            #it's a multichat
            #that cid must be exists
            if conv in self.rpapyconv:
                cid = self.rpapyconv[conv]
                self.session.conv_contact_joined(cid, account)
            else:
                #TODO dialog error????
                log.error("Error inviting user to conversation")

    def _on_conversation_user_left(self, papycontact, pyconvevent):
        '''handle user left event'''
        account = papycontact.account
        conv = pyconvevent.conversation

        #that cid must exists
        if conv in self.rpapyconv:
            cid = self.rpapyconv[conv]

            self.session.conv_contact_left(cid, account)

    # contact changes handlers
    def _on_contact_membership_changed(self, papycontact):
        log.info("Contact membership changed: %s" % papycontact)
        contact = self.session.contacts.contacts.get(papycontact.account, None)

        if not contact:
            self._add_contact(papycontact)
            self.session.contact_add_succeed(papycontact.account)
        else:
            self.session.contact_attr_changed(papycontact.account,
                    'membership', None)

    def _on_contact_status_changed(self, papycontact):
        status_ = STATUS_PAPY_TO_E3[papycontact.presence]
        contact = self.session.contacts.contacts.get(papycontact.account, None)

        if not contact:
            return

        account = contact.account
        old_status = contact.status
        contact.status = status_
        self.session.contact_attr_changed(account, 'status', old_status)

        acc = Logger.Account(contact.cid,
                    None, contact.account, contact.status, contact.nick,
                    contact.message, contact.picture)
        self.session.log('status change', contact.status,
                    old_status, acc)

    def _on_contact_nick_changed(self, papycontact):
        contact = self.session.contacts.contacts.get(papycontact.account, None)

        if not contact:
            return

        account = contact.account
        old_nick = contact.nick
        nick = papycontact.display_name
        contact.nick = nick
        status_ = contact.status

        log_account = Logger.Account(contact.cid, None,
            contact.account, contact.status, contact.nick, contact.message,
            contact.picture)

        if old_nick != nick:
            self.session.contact_attr_changed(account, 'nick', old_nick)
            self.session.log('nick change', status_, nick,
                log_account)

    def _on_contact_pm_changed(self, papycontact):
        contact = self.session.contacts.contacts.get(papycontact.account, None)

        if not contact:
            return

        account = contact.account
        old_message = contact.message
        contact.message = papycontact.personal_message

        if old_message == contact.message:
            return

        if old_message != contact.message:
            self.session.contact_attr_changed(account, 'message', old_message)
            self.session.log('message change', contact.status,
                contact.message, Logger.Account(contact.cid,
                    None, contact.account, contact.status, contact.nick,
                    contact.message, contact.picture))

    def _on_contact_media_changed(self, papycontact):
        contact = self.session.contacts.contacts.get(papycontact.account, None)
        if not contact:
            return
        account = contact.account
        old_media = contact.media

        contact.media = ' - '.join(papycontact.current_media) \
                            if papycontact.current_media and \
                            papycontact.current_media != ('', '') else ''

        if contact.media != '':
            contact.media = '♫ ' + contact.media

        if old_media == contact.media:
            return

        self.session.contact_attr_changed(account, 'media', old_media)
        # TODO: log the media change

    def _on_contact_msnobject_changed(self, contact):
        '''called when a contact changes his display picture'''
        msn_object = contact.msn_object
        if msn_object is None:
            if STATUS_PAPY_TO_E3[contact.presence] != status.OFFLINE:
                ctct = self.session.contacts.get(contact.account)
                ctct.picture = None
                avatars = self.session.caches.get_avatar_cache(contact.account)
                if 'last' in avatars:
                    try:
                        os.remove(os.path.join(avatars.path, 'last'))
                    except OSError, e:
                        log.warning("Last picture remove failed: %s" % e)
                self.session.picture_change_succeed(contact.account, None)
            return
        if msn_object._type == papyon.p2p.MSNObjectType.DISPLAY_PICTURE:
            avatars = self.session.caches.get_avatar_cache(contact.account)
            avatar_hash = msn_object._data_sha.encode("hex")
            avatar_path = os.path.join(avatars.path, avatar_hash)
            ctct = self.session.contacts.get(contact.account)

            if avatar_hash in avatars:
                if ctct:
                    ctct.picture = avatar_path

                self.session.picture_change_succeed(contact.account,
                        avatar_path)

                return avatar_path

            def download_failed(reason):
                log.error("Error downloading display picture: %s" % reason)

            def download_ok(msnobj, callback):
                avatars.insert_raw(msnobj._data)

                if ctct:
                    ctct.picture = avatar_path

                self.session.picture_change_succeed(contact.account,
                        avatar_path)

            if msn_object._type not in (
                    papyon.p2p.MSNObjectType.DYNAMIC_DISPLAY_PICTURE,
                    papyon.p2p.MSNObjectType.DISPLAY_PICTURE):
                return

            self.msn_object_store.request(msn_object,
                (download_ok, download_failed), peer=contact)

    # address book events
    def _on_addressbook_contact_pending(self, contact):
        log.debug("contact pending: %s" % contact)
        # Add to the pending contacts
        tmp_cont = e3.base.Contact(contact.account, contact.id,
            contact.display_name, contact.personal_message,
            STATUS_PAPY_TO_E3[contact.presence], '',
            (papyon.profile.Membership.BLOCK & contact.memberships))
        self.session.contacts.pending[contact.account] = tmp_cont
        self.session.contact_added_you()

    def _on_addressbook_messenger_contact_added(self, contact):
        self._add_contact(contact)
        # We handle this in the respective callbacks.
        #self.session.contact_add_succeed(contact.account)
        return

    def _on_addressbook_contact_deleted(self, contact):
        self.session.contact_remove_succeed(contact.account)

    def _on_addressbook_contact_blocked(self, contact):
        self._block_contact(contact)
        self.session.contact_block_succeed(contact.account)

    def _on_addressbook_contact_unblocked(self, contact):
        self._unblock_contact(contact)
        self.session.contact_unblock_succeed(contact.account)

    def _on_addressbook_group_added(self, group):
        self._add_group(group)
        self.session.group_add_succeed(group.id)

    def _on_addressbook_group_deleted(self, group):
        self.session.group_remove_succeed(group.id)

    def _on_addressbook_group_renamed(self, group):
        self._rename_group(group)
        self.session.group_rename_succeed(group.id)

    def _on_addressbook_group_contact_added(self, group, contact):
        self._add_contact_to_group(contact, group)
        self.session.group_add_contact_succeed(group.id,
                contact.id)

    def _on_addressbook_group_contact_deleted(self, group, contact):
        self._remove_contact_from_group(contact, group)
        self.session.group_remove_contact_succeed(group.id, contact.id)

    # profile events
    def _on_profile_presence_changed(self):
        """Called when the presence changes."""
        stat = STATUS_PAPY_TO_E3[self.profile.presence]
        self.session.account.status = stat
        # log the status
        contact = self.session.contacts.me
        account = Logger.Account(contact.cid, None, contact.account, stat,
                contact.nick, contact.message, contact.picture)

        self.session.log('status change', stat, str(stat), account)
        self.session.status_change_succeed(stat)

    def _on_profile_display_name_changed(self):
        """Called when the display name changes."""
        display_name = self.profile.display_name
        self.session.contacts.me.nick = display_name

        contact = self.session.contacts.me
        account = Logger.Account(contact.cid, None, contact.account,
                contact.status, display_name, contact.message, contact.picture)

        self.session.log('nick change', contact.status, display_name,
                account)
        self.session.nick_change_succeed(display_name)

    def _on_profile_personal_message_changed(self):
        """Called when the personal message changes."""
        message = self.profile.personal_message
        # set the message in emesene
        self.session.contacts.me.message = message
        # log the change
        contact = self.session.contacts.me
        account = Logger.Account(contact.cid, None, contact.account,
                contact.status, contact.nick, contact.message, contact.picture)

        self.session.log(
            'message change', contact.status, message, account)
        self.session.message_change_succeed(message)

    def _on_profile_current_media_changed(self):
        """Called when the current media changes."""
        if(self.profile.current_media is not None):
            message = "♫ " + self.profile.current_media[0] + " - " + self.profile.current_media[1]
        else:
            message = self.profile.personal_message
        # set the message in emesene
        self.session.contacts.me.message = message
        # log the change
        contact = self.session.contacts.me
        account = Logger.Account(contact.cid, None,
            contact.account, contact.status, contact.nick, contact.message,
            contact.picture)

        self.session.log('message change', contact.status, message, account)
        self.session.message_change_succeed(message)

    def _on_profile_msn_object_changed(self):
        """Called when the MSNObject changes."""
        msn_object = self.profile.msn_object
        if msn_object is not None:
            self._handle_action_set_picture(msn_object)

    # mailbox handlers
    def _on_mailbox_unread_mail_count_changed(self, unread_mail_count,
            initial):

        log.info("Mailbox count changed (initial? %s): %s" % (initial,
            unread_mail_count))
        self.session.mail_count_changed(unread_mail_count)

    def _on_mailbox_new_mail_received(self, mail_message):
        log.info("New mailbox message received: %s" % mail_message)
        self.session.mail_received(mail_message)
        ''' MAIL MESSAGE:
        def name(self):
        """The name of the person who sent the email"""
        def address(self):
        """Email address of the person who sent the email"""
        def post_url(self):
        """post url"""
        def form_data(self):
        """form url"""
        return self._form_data '''

    def _on_profile_end_point_added(self, ep):
        """Called when endpoints change (added)"""
        if ep.id != self.machine_guid:
            self.session.endpoint_added(ep.id, ep.name)

    def _on_profile_end_point_removed(self, ep):
        """Called when endpoints change (removed)"""
        self.session.endpoint_removed(ep.id)

    def _on_profile_end_point_updated(self, ep):
        """Called when endpoints change (updated)"""
        if ep.id != self.machine_guid:
            self.session.endpoint_updated(ep.id, ep.name)

################################################################################
# BELOW THIS LINE, ONLY e3 HANDLERS
################################################################################

    # e3 action handlers
    def _handle_action_login(self, account, password, status_, host, port):
        '''handle Action.ACTION_LOGIN '''
        self.session.account.account = account
        self.session.account.password = password
        self.session.account.status = status_

        self.session.login_started()
        self.login(account, password)

    def _handle_action_logout(self):
        ''' handle Action.ACTION_LOGOUT '''
        self.session.close()

    def _handle_action_quit(self):
        '''handle Action.ACTION_QUIT
        '''
        #chain up to base class
        e3.base.Worker._handle_action_quit(self)
        if not self.state == papyon.event.ClientState.CLOSED:
            self.logout()

    # e3 action handlers - address book
    def _handle_action_add_contact(self, account):
        ''' handle Action.ACTION_ADD_CONTACT '''
        def add_contact_fail(ab_error):
            log.error("Error adding a contact: %s", ab_error)
            self.session.contact_add_failed('')

        def add_contact_succeed(contact):
            self.session.contact_add_succeed(contact.account)

        papycontact = self.contact_by_account(account)
        if account in self.session.contacts.pending:
            if papycontact is None:
                return
            self.address_book.accept_contact_invitation(
                papycontact, add_to_contact_list=True,
                done_cb=(add_contact_succeed,),
                failed_cb=(add_contact_fail,))
        else:
            if papycontact is not None:
                n_id = papycontact.network_id
            else:
                n_id = papyon.profile.NetworkID.MSN
            self.address_book.add_messenger_contact(account,
                network_id=n_id,
                done_cb=(add_contact_succeed,),
                failed_cb=(add_contact_fail,))

    def _handle_action_add_group(self, name):
        '''handle Action.ACTION_ADD_GROUP '''
        def add_group_fail(*args):
            log.error("Error adding a group: %s", args)
            #group name
            self.session.group_add_failed('')

        def add_group_succeed(*args):
            #group id
            self.session.group_add_succeed(args[0].id)

        callback_vect = [add_group_succeed,name]
        self.address_book.add_group(name, failed_cb=add_group_fail,
                done_cb=tuple(callback_vect))

    def _handle_action_add_to_group(self, account, gid):
        ''' handle Action.ACTION_ADD_TO_GROUP '''
        def add_to_group_fail(*args):
            log.error("Error adding a contact to a group: %s", args)
            #gid, cid
            self.session.group_add_contact_failed(0, 0)

        def add_to_group_succeed(papycontact, gid):
            self.session.group_add_contact_succeed(gid, papycontact.account)

        def copy_to_group_succeed(papygroup, papycontact, gid):
            self.session.group_add_contact_succeed(gid, papycontact.account)

        papygroupdest = None
        for group in self.address_book.groups:
            if group.id == self.session.groups[gid].identifier:
                papygroupdest = group

        if papygroupdest is not None:
            papycontact = self.contact_by_account(account)

            if papycontact is None: #We don't have it in the address book
                self.address_book.add_messenger_contact(account,
                        groups=[papygroupdest],
                        done_cb=(add_to_group_succeed, gid),
                        failed_cb=(add_to_group_fail,))
            else:
                self.address_book.add_contact_to_group(papygroupdest,
                        papycontact, done_cb=(copy_to_group_succeed, gid),
                        failed_cb=(add_to_group_fail,))

    def _handle_action_block_contact(self, account):
        ''' handle Action.ACTION_BLOCK_CONTACT '''
        def block_fail(*args):
            log.error("Error blocking a contact: %s", args)
            # account
            self.session.contact_block_failed('')

        papycontact = self.contact_by_account(account)

        self.address_book.block_contact(papycontact, failed_cb=(block_fail,))

    def _handle_action_unblock_contact(self, account):
        '''handle Action.ACTION_UNBLOCK_CONTACT '''

        def unblock_fail(*args):
            log.error("Error unblocking a contact: %s", args)
            # account
            self.session.contact_unblock_failed('')

        papycontact = self.contact_by_account(account)
        self.address_book.unblock_contact(papycontact,
                failed_cb=(unblock_fail,))

    def _handle_action_move_to_group(self, account, src_gid, dest_gid):
        '''handle Action.ACTION_MOVE_TO_GROUP '''
        def move_to_group_fail(*args):
            log.error("Error moving a contact: %s", args)
            # account
            self.session.contact_move_failed('')

        def add_to_group_succeed(group, contact):
            if papygroupsrc is not None:
                self.address_book.delete_contact_from_group(papygroupsrc,
                        papycontact, done_cb=(move_to_group_succeed,),
                        failed_cb=(move_to_group_fail,))
            else:
                self.session.contact_remove_succeed(contact.account)

            self.session.group_add_contact_succeed(group.id, contact.account)

        def move_to_group_succeed(group, contact):
            self.session.group_remove_contact_succeed(group.id, contact.account)

        papycontact = self.contact_by_account(account)
        papygroupdest = None
        papygroupsrc = None

        if src_gid != '0':
            for group in self.address_book.groups:
                if group.id == self.session.groups[src_gid].identifier:
                    papygroupsrc = group

        for group in self.address_book.groups:
            if group.id == self.session.groups[dest_gid].identifier:
                papygroupdest = group

        if papygroupdest is not None:
            self.address_book.add_contact_to_group(papygroupdest,
                    papycontact,done_cb=(add_to_group_succeed,),
                    failed_cb=(move_to_group_fail,))

    def _handle_action_remove_contact(self, account):
        '''handle Action.ACTION_REMOVE_CONTACT '''
        def remove_contact_fail(*args):
            log.error("Error when removing contact: %s" % args)
            self.session.contact_remove_failed('')

        def remove_contact_succeed(contact):
            self.session.contact_remove_succeed(contact.account)

        papycontact = self.contact_by_account(account)

        self.address_book.delete_contact(papycontact,
                done_cb=(remove_contact_succeed,),
                failed_cb=(remove_contact_fail,))

    def _handle_action_reject_contact(self, account): #TODO: finish this
        '''handle Action.ACTION_REJECT_CONTACT '''
        papycontact = self.contact_by_account(account)
        self.address_book.decline_contact_invitation(papycontact)

        # TODO: move to ab callback
        self.session.contact_reject_succeed(account)

    def _handle_action_remove_from_group(self, account, gid):
        ''' handle Action.ACTION_REMOVE_FROM_GROUP '''
        def remove_from_group_fail(*args):
            log.error("Error when removing contact from group: %s" % args)
            self.session.group_remove_contact_failed('')

        def remove_from_group_succeed(group, contact):
            self.session.group_remove_contact_succeed(group.id, contact.account)

            if len(contact.groups) == 0: # Add to "No Group" group.
                self.session.contact_add_succeed(contact.account)

        papycontact = self.contact_by_account(account)
        papygroup = None

        for group in self.address_book.groups:
            if group.id == self.session.groups[gid].identifier:
                papygroup = group

        if papygroup is not None:
            self.address_book.delete_contact_from_group(papygroup, papycontact,
                    done_cb=(remove_from_group_succeed,),
                    failed_cb=(remove_from_group_fail,))

    def _handle_action_remove_group(self, gid):
        ''' handle Action.ACTION_REMOVE_GROUP '''
        def remove_group_fail(*args):
            log.error("Error when removing group: %s" % args)
            self.session.group_remove_failed(0) #gid

        papygroup = None

        for group in self.address_book.groups:
            if group.id == self.session.groups[gid].identifier:
                papygroup = group

        if papygroup is not None:
            self.address_book.delete_group(papygroup,
                    failed_cb=(remove_group_fail,))

    def _handle_action_rename_group(self, gid, name):
        ''' handle Action.ACTION_RENAME_GROUP '''
        def rename_group_fail(*args):
            log.error("Error when renaming group: %s" % args)
            # gid, name
            self.session.group_rename_failed(0, '')

        papygroup = None
        for group in self.address_book.groups:
            if group.id == self.session.groups[gid].identifier:
                papygroup = group

        if papygroup is not None:
            self.address_book.rename_group(papygroup, name,
                    failed_cb=(rename_group_fail,))

    def _handle_action_set_contact_alias(self, account, alias):
        ''' handle Action.ACTION_SET_CONTACT_ALIAS '''

        contact = self.session.contacts.contacts.get(account, None)
        if not contact:
            return

        account = contact.account
        old_nick = contact.nick

        def set_contact_alias_fail(*args):
            log.error("Error when settings alias: %s" % args)
            # account
            self.session.contact_alias_failed('')

        def set_contact_alias_succeed(papycontact, *args):
            log.info("Setting alias ok: %s" % account)

            if alias != "":
                contact.alias = papycontact.alias
                contact.nick = papycontact.alias
            else:
                contact.alias = ""
                contact.nick = papycontact.display_name

            self.session.contact_attr_changed(account, 'nick', old_nick)

        papycontact = self.contact_by_account(account)
        new_alias = alias.encode("utf-8")
        constants = papyon.service.description.AB.constants

        infos = {constants.ContactGeneral.ANNOTATIONS:
                {constants.ContactAnnotations.NICKNAME: new_alias}}

        self.address_book.update_contact_infos(papycontact, infos,
            done_cb=(set_contact_alias_succeed,),
                failed_cb=set_contact_alias_fail)

    def _handle_action_change_status(self, status_):
        '''handle Action.ACTION_CHANGE_STATUS '''
        self._set_status(status_)

    def _handle_action_set_media(self, message):
        '''handle Action.ACTION_SET_MEDIA
        '''
        e3.base.Worker._handle_action_set_media(self, message)
        self.profile.personal_message_current_media = self.profile.personal_message, message

    def _handle_action_set_message(self, message):
        ''' handle Action.ACTION_SET_MESSAGE '''
        if message is None:
            message = ''
        nick = self.profile.display_name
        self.profile.personal_message = message
        self.content_roaming.store(xml.sax.saxutils.escape(nick),
                xml.sax.saxutils.escape(message), None)

    def _handle_action_set_nick(self, nick):
        '''handle Action.ACTION_SET_NICK '''
        self.profile.display_name = nick
        message = self.profile.personal_message
        self.content_roaming.store(xml.sax.saxutils.escape(nick),
                xml.sax.saxutils.escape(message), None)

    def _handle_action_set_picture(self, picture_name, from_roaming=False):
        '''handle Action.ACTION_SET_PICTURE'''
        if isinstance(picture_name, papyon.p2p.MSNObject):
            #TODO: check if this can happen, and prevent it (!)
            return

        if picture_name == '':
            self.profile.msn_object = None
            self.session.contacts.me.picture = None
            self.session.picture_change_succeed(self.session.account.account, None)
            return

        avatar = self._filedata_to_string(picture_name)

        msn_object = papyon.p2p.MSNObject(self.profile, len(avatar),
                papyon.p2p.MSNObjectType.DISPLAY_PICTURE,
                hashlib.sha1(avatar).hexdigest() + '.tmp', "",
                data=StringIO.StringIO(avatar))

        self.profile.msn_object = msn_object
        avatar_hash = msn_object._data_sha.encode("hex")
        avatar_path = os.path.join(self.my_avatars.path, avatar_hash)

        if avatar_hash in self.my_avatars:
            self.session.picture_change_succeed(self.session.account.account,
                    avatar_path)
        else:
            self.my_avatars.insert_raw(msn_object._data)
            self.session.picture_change_succeed(self.session.account.account,
                    avatar_path)

        self.session.contacts.me.picture = avatar_path

        if not from_roaming:
            nick = self.profile.display_name
            message = self.profile.personal_message
            self.content_roaming.store(xml.sax.saxutils.escape(nick),
                    xml.sax.saxutils.escape(message), avatar)

    def _handle_action_new_conversation(self, account, cid):
        ''' handle Action.ACTION_NEW_CONVERSATION '''
        # % { 'ci' : cid, 'acco' : account }
        # append cid to emesene conversations

        if account in self.conversations:
            #print "there's already a conversation with this user wtf"
            # update cid and close the old conversation
            oldcid = self.conversations[account]
            self._handle_action_close_conversation(oldcid)

        self.conversations[account] = cid
        self.rconversations[cid] = account
        # create a papyon conversation
        contact = self.contact_by_account(account)
        conv = papyon.Conversation(self, [contact,])
        self.papyconv[cid] = conv
        self.rpapyconv[conv] = cid
        # attach the conversation event handler
        convhandler = ConversationEvent(conv, self)
        self._conversation_handler[cid] = convhandler

    def _handle_action_close_conversation(self, cid):
        '''handle Action.ACTION_CLOSE_CONVERSATION
        '''
        #print "you close conversation %f, are you happy?" % cid
        account = self.rconversations[cid]
        conv = self.papyconv[cid]
        conv.leave()
        del self.conversations[account]
        del self.rconversations[cid]
        del self.papyconv[cid]
        del self.rpapyconv[conv]
        del self._conversation_handler[cid]
        self.session.conv_ended(cid)

    def _handle_action_conv_invite(self, cid, account):
        '''handle Action.ACTION_CONV_INVITE
        '''
        conv = self.papyconv[cid]
        papycontact = self.contact_by_account(account)
        if papycontact not in conv.participants:
            conv._invite_user(papycontact)

    def _handle_action_send_message(self, cid, message,
            cedict=None, l_custom_emoticons=None):
        ''' handle Action.ACTION_SEND_MESSAGE '''
        # find papyon conversation by cid

        # Handle super-long messages that destroy the switchboard
        if message.type == e3.base.Message.TYPE_MESSAGE:
            if len(message.body) > 1500:
                def split_len(seq, length):
                    return [seq[i:i+length] for i in range(0, len(seq), length)]
                parts = split_len(message.body, 1500)
                new_msg = message
                for part in parts:
                    new_msg.body = part
                    self._handle_action_send_message(cid, new_msg, cedict,
                            l_custom_emoticons)
                return

        papyconversation = self.papyconv[cid]

        if len(papyconversation.total_participants) == 1:
            for first_dude in papyconversation.total_participants:
                break

            try:
                switchboard = papyconversation.switchboard
            except AttributeError:
                switchboard = None

            if first_dude.presence == papyon.Presence.OFFLINE and \
               first_dude.network_id != papyon.profile.NetworkID.EXTERNAL:
                if switchboard is None or \
                   switchboard.state != papyon.msnp.ProtocolState.OPEN:

                    if message.type == e3.base.Message.TYPE_NUDGE or \
                       message.type == e3.base.Message.TYPE_TYPING:
                        return
                    else:
                        self.oim_box.send_message(first_dude, message.body)
                        # don't process this.
                        message.type = e3.base.Message.TYPE_FLNMSG

        if message.type == e3.base.Message.TYPE_NUDGE:
            papyconversation.send_nudge()

        elif message.type == e3.base.Message.TYPE_TYPING:
            papyconversation.send_typing_notification()

        elif message.type == e3.base.Message.TYPE_MESSAGE:
            # format the text for papy
            formatting = formatting_e3_to_papy(message.style)
            emoticon_cache = self.session.caches.get_emoticon_cache(
                    self.session.account.account)
            d_msn_objects = {}

            if cedict is None: cedict = {}
            if l_custom_emoticons is None: l_custom_emoticons = []

            for custom_emoticon in l_custom_emoticons:

                fpath = os.path.join(emoticon_cache.path, cedict[custom_emoticon])
                d_custom_emoticon = self._filedata_to_string(fpath)

                msn_object = papyon.p2p.MSNObject(self.session.account.account,
                        len(d_custom_emoticon),
                        papyon.p2p.MSNObjectType.CUSTOM_EMOTICON,
                        cedict[custom_emoticon], custom_emoticon, None, None,
                        data=StringIO.StringIO(d_custom_emoticon))

                d_msn_objects[custom_emoticon] = msn_object
            # create papymessage
            msg = papyon.ConversationMessage(message.body, formatting,
                    d_msn_objects)
            # send through the network
            papyconversation.send_text_message(msg)
        else:
            return # Do NOT process other message types

        members = [x.account for x in papyconversation.total_participants]
        e3.Logger.log_message(self.session, members, message, True, cid = cid)

    # ft handlers
    def _handle_action_ft_invite(self, cid, account, filename, completepath,
            preview_data):

        if not os.path.getsize(completepath):
            # size is 0 or function returned None, 
            # don't try to send anything
            return

        papycontact = self.contact_by_account(account)
        papysession = self.ft_manager.send(papycontact, filename,
                os.path.getsize(completepath), preview_data)

        tr = e3.base.FileTransfer(papysession, papysession.filename,
                papycontact, papysession.size, papysession.preview,
                sender='Me', completepath=completepath)

        self.filetransfers[papysession] = tr
        self.rfiletransfers[tr] = papysession

        papysession.connect("accepted", self.papy_ft_accepted)
        papysession.connect("progressed", self.papy_ft_progressed)
        papysession.connect("completed", self.papy_ft_completed)
        papysession.connect("rejected", self.papy_ft_rejected)
        papysession.connect("canceled", self.papy_ft_canceled)

        self.session.filetransfer_invitation(tr, cid)

    def _handle_action_ft_accept(self, t):
        self.rfiletransfers[t].accept()

    def _handle_action_ft_reject(self, t):
        self.rfiletransfers[t].reject()

        del self.filetransfers[self.rfiletransfers[t]]
        del self.rfiletransfers[t]

    def _handle_action_ft_cancel(self, t):
        self.rfiletransfers[t].cancel()

        del self.filetransfers[self.rfiletransfers[t]]
        del self.rfiletransfers[t]

    # call handlers
    def _handle_action_call_invite(self, cid, account, a_v_both, surface_other,
            surface_self):
        return # We don't have the codecs in gstreamer, sorry.
        papycontact = self.contact_by_account(account)
        papysession = self.call_manager.create_call(papycontact)
        call_handler = CallEvent(papysession, self)
        session_handler = PapyConference.MediaSessionHandler(
            papysession.media_session, surface_other, surface_self)
        log.info("Call %s - %s" % (account, a_v_both))

        if a_v_both == 0: # see gui.base.Conversation.py 0=V,1=A,2=AV
            streamv = papysession.media_session.create_stream("video",
                         papyon.media.constants.MediaStreamDirection.BOTH, True)
            papysession.media_session.add_stream(streamv)
        elif a_v_both == 1:
            streama = papysession.media_session.create_stream("audio",
                         papyon.media.constants.MediaStreamDirection.BOTH, True)
            papysession.media_session.add_stream(streama)
        elif a_v_both == 2:
            streamv = papysession.media_session.create_stream("video",
                         papyon.media.constants.MediaStreamDirection.BOTH, True)
            papysession.media_session.add_stream(streamv)
            streama = papysession.media_session.create_stream("audio",
                         papyon.media.constants.MediaStreamDirection.BOTH, True)
            papysession.media_session.add_stream(streama)

        ca = e3.base.Call(papysession, papycontact.account)
        self.calls[papysession] = ca
        self.rcalls[ca] = papysession

        papysession.invite()
        self.session.call_invitation(ca, cid, True)

    def _handle_action_call_accept(self, c):
        session_handler = PapyConference.MediaSessionHandler(
            c.object.media_session, c.surface_buddy, c.surface_self)

        self.rcalls[c].accept()

    def _handle_action_call_reject(self, c):
        self.rcalls[c].reject()

        del self.calls[self.rcalls[c]]
        del self.rcalls[c]

    def _handle_action_call_cancel(self, c):
        self.rcalls[c].end()

        del self.calls[self.rcalls[c]]
        del self.rcalls[c]

    def _handle_action_disconnect_other_endpoints(self):
        """ disconnects all other msnp18 mpop instances """
        self.disconnect_other_endpoints()

    def _handle_action_disconnect_endpoint(self, ep_id):
        """ disconnects a single msnp18 mpop instance """
        if ep_id == "":
            self.disconnect_other_endpoints()
            return
        self.disconnect_endpoint(ep_id)

    def _handle_action_set_endpoint_name(self, ep_name):
        '''handle Action.ACTION_SET_ENDPOINT_NAME'''
        self.profile.end_point_name = ep_name
