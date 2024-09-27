"""
A Taurus based TANGO backend for the SVG synoptic.
"""

from inspect import isclass
import json

import numpy as np
from PyQt4 import QtCore
import PyTango
from synopticwidget import SynopticWidget
from taurus import Attribute, Manager, Release
from taurus.core.taurusbasetypes import (
    AttrQuality, TaurusEventType, TaurusSerializationMode)
from taurus.external.qt import Qt
TAURUS_VERSION = Release.version_info[0]
try:
    from taurus.qt.qtgui.container import TaurusWidget
except ImportError:
    from taurus.qt.qtgui.panel import TaurusWidget
from taurus.qt.qtgui.panel import TaurusDevicePanel
from taurus.qt.qtgui.application import TaurusApplication
try:
    from taurus.core.tango.enums import DevState
except ImportError:
    from PyTango import DevState, AttrQuality

from .taurusregistry import Registry


class TooltipUpdater(QtCore.QThread):

    finished = QtCore.pyqtSignal(str, str)

    def __init__(self, model):
        QtCore.QThread.__init__(self)
        self.model = model

    def run(self):
        # TODO: This needs to be redone; maybe use string.template?
        try:
            attribute = Attribute(str(self.model) + "/State")
            if attribute:
                value = attribute.read()
                html = json.dumps('Value:&nbsp;' +
                                  '<span class="value">%s</span>'
                                  % value.value)
            self.finished.emit(self.model, html)
        except PyTango.DevFailed as e:
            print e


def getStateClasses(state=None):
    "Return a state CSS class configuration"
    states = dict((("state-%s" % name), s == state)
                  for name, s in PyTango.DevState.names.items())
    states["state"] = True
    return states


# precalculate since this is done quite a lot
STATE_CLASSES = dict((state, json.dumps(getStateClasses(state)))
                     for name, state in PyTango.DevState.names.items())
STATE_CLASSES[None] = json.dumps(getStateClasses())

# lookup table to get state name as a string
STATE_MAP = {code: name for name, code in PyTango.DevState.names.items()}


class TaurusSynopticWidget(SynopticWidget, TaurusWidget):

    """A SynopticWidget that connects to TANGO in order to
    get updates for models (attributes)."""

    tooltip_trigger = QtCore.pyqtSignal(str)

    def __init__(self, parent=None, **kwargs):
        super(TaurusSynopticWidget, self).__init__(parent=parent)
        print('init TaurusSynopticWidget')
        Manager().setSerializationMode(TaurusSerializationMode.Serial)
        self.tooltip_trigger.connect(self._update_device_tooltip)
        self._panels = {}

    def setModel(self, image, section=None):
        self.set_url(image)
        self.registry = Registry(self.attribute_listener,
                                 self.unsubscribe_listener)
        self.registry.start()
        self.js.plugin_command.connect(self.run_plugin_command)
        self.js.hovered.connect(self._hovered)
        # self._tooltip_data = {}
        # self.tooltip_registry = Registry(self._tooltip_updater)
        # self.tooltip_registry.start()

    def _hovered(self, sec, mods):
        attr = self.registry.get_listener(str(mods))

    def getModel(self):
        return self._url

    def closeEvent(self, event):
        "Clean things up when the widget is closed"
        self.registry.clear()
        self.registry.stop()
        self.registry.wait()
        self.registry = None

    def run_plugin_command(self, plugin, cmd, args):
        try:
            plugins = __import__("plugins.%s" % plugin, globals(),
                                 locals(), [cmd], -1)
        except ImportError as e:
            print "Could not initialize plugin '%s'!" % plugin
            print e
            return ""
        return getattr(plugins, cmd)(self, args)

    def handle_subscriptions(self, models=[]):
        print "handle_subscriptions ", models
        if self.registry:
            self.registry.subscribe(models)

    def unsubscribe_listener(self, unsubscribed):
        """Tell the synoptic about unsubscribed models. This is
        needed because it's all asunchronous so it cannot be assumed
        that a model is really unsubscribed until it is."""
        classes = STATE_CLASSES[None]
        for model in unsubscribed:
            self.js.evaluate("synoptic.setClasses('model', %r, %s)" %
                             (model, classes))

    def filter_fragment(self, model, value):
        # the "fragment" is an optional part at the end of an eval attribute,
        # prefixed by a "#" and intended to make it possible to "filter" only
        # part of a spectrum or image through "slicing". It is up to the
        # client to do this, so we implement it here.
        frag = self.registry.eval_validator.getNames(model, fragment=True)[3]
        if frag:
            indices = frag[1:-1]
            try:
                # this is the special case where the slice is just an index
                # I'm not 100% that this should be allowed, but it's so useful!
                index = int(indices)
                return value[index]
            except ValueError:
                pass
            try:
                slice_ = [int(i) for i in indices.split(":")]
                return value[slice(*slice_)]
            except ValueError:
                pass
        return value

    def set_custom_value(self, model, attr_value):
        # this is the special case where value is modified for PyAlarm class
        # when alarm is trigered PyAlarm attribute returns 'True' value, icon on synoptic is green
        # set_custom_value change value for better alarms visualisation
        # function can be customised for any other tango device classes
        if 'PyAlarm' in PyTango.get_device_proxy(model).info().dev_class:
            return not(attr_value)
        else:
            return attr_value

    def attribute_listener(self, model, evt_src, evt_type, evt_value):
        "Handle events"
        if evt_type == TaurusEventType.Error:
            return  # handle errors somehow
        if evt_type == TaurusEventType.Config:
            return  # need to do something here too
        value = evt_value.value
        # check for the presence of a "fragment" ending (e.g. ...#[3])
        if self.registry and self.registry.eval_validator.isValid(model):
            try:
                value = self.filter_fragment(model, value)
            except Exception:
                pass  # fragment is a taurus 4 feature

        # handle the value differently depending on what it is
        # TODO: clean this up!

        quality = evt_value.quality
        quality_string = str(PyTango.AttrQuality.values[quality])

        if isinstance(value, (DevState, PyTango.DevState,
                              PyTango._PyTango.DevState)):
            classes = STATE_CLASSES[value]
            device, attr = model.rsplit("/", 1)
            state = STATE_MAP[value]
            data = {"value": state, "quality": quality_string}

            if attr.lower() == "state":
                # this is the normal "State" attribute of the
                # device. Let's set the color of device models
                # too, for convenience.
                self.js.evaluate("synoptic.setClasses('model', %r, %s)" %
                                 (device, classes))
                self.js.evaluate("synoptic.setClasses('model', '%s/State', %s)" %
                                 (device, classes))
                self.js.evaluate("synoptic.setData('model', %r, %r)" %
                                 (device, data))
                self.js.evaluate("synoptic.setData('model', '%s/State', %r)" %
                                 (device, data))
            else:
                # Apparently it's an attribute of type DevState
                # but it is not the normal "State" attribute.
                self.js.evaluate("synoptic.setClasses('model', %r, %s)" %
                                 (model, classes))
                self.js.evaluate("synoptic.setData('model', %r, %s)" %
                                 (model, data))

            self.js.evaluate("synoptic.setText('model', %r, '%s')" %
                             (model, value))

        elif isinstance(value, (bool, np.bool_)):
            #Change value 
            value = self.set_custom_value('/'.join(model.split('/')[:-1]), value)
            classes = {"boolean": True,
                       "boolean-true": bool(value),
                       "boolean-false": not value}
            self.js.evaluate("synoptic.setClasses('model', %r, %s)" %
                             (model, json.dumps(classes)))
            self.js.evaluate("synoptic.setText('model', %r, '%s')" %
                             (model, value))
            self.js.evaluate("synoptic.setData('model', %r, %s)" %
                             (model,
                              json.dumps({"value": str(value).lower(),
                                          "quality": quality_string})))
        else:
            # everything else needs to be displayed as text
            if quality == PyTango.AttrQuality.ATTR_INVALID:
                text = "?"  # do something more sophisticated here
            else:
                # TODO: need more sophisticated logic here; currently
                # spectrums/images break completely. I'm not sure
                # we should even support those...
                try:
                    if TAURUS_VERSION == 4:
                        fmt = evt_src.getFormat()
                        value = fmt%value    # taurus4 issue: values without format
                    text = evt_src.displayValue(value)
                except AttributeError:
                    text = str(value)
            try:
                unit = evt_src.getConfig().unit
            except AttributeError:
                unit = None

            if unit in (None, "No unit"):
                unit = ""
            self.js.evaluate("synoptic.setText('model', %r, '%s %s')" %
                             (model, text, unit))
            self.js.evaluate("synoptic.setData('model', %r, %s)" %
                             (model, json.dumps({"value": text,
                                                 "quality": quality_string})))

    def on_click(self, kind, name):
        """The default behavior is to mark a clicked device and to zoom to a
        clicked section.  Override this function if you need something
        else.
        """
        print "on_click", kind, name
        if kind == "model" and self.registry.device_validator.isValid(name):
            self.select(kind, [name])
            self.emit(Qt.SIGNAL("graphicItemSelected(QString)"), name)
        elif kind == "section":
            self.zoom_to(kind, name)
        else:
            self.unselect_all()

    def get_device_panel(self, device):
        """Override to change which panel is opened for a given device
        name. Return a widget class, a widget, or None if you're
        handling the panel yourself. TaurusDevicePanel is a reasonable
        fallback.
        """
        return TaurusDevicePanel

    def on_rightclick(self, kind, name):
        "The default behavior for right clicking a device is to open a panel."
        if kind == "model" and self.registry.device_validator.isValid(name):
            if name.lower() in self._panels:

                widget = self._panels[name.lower()]
                print "Found existing panel for %s:" % name, widget
                if not widget.isVisible():
                    widget.show()
                widget.activateWindow()
                widget.raise_()
                return


            # check if we recognise the class of the device
            widget = self.get_device_panel(name)

            if not widget:
                # assume that the widget is handled somewhere else
                return
            if isclass(widget):
                # assume it's a widget class
                widget = widget()
                try:
                    # try to set the model
                    widget.setModel(name)
                except AttributeError:
                    pass

            widget.setWindowTitle(name)
            # monkey patch to cleanup on close...
            widget.closeEvent = lambda _: self._cleanup_panel(widget)
            # keep a reference to the widget
            self._panels[name.lower()] = widget
            widget.show()

    def _cleanup_panel(self, w):
        """In the long run it seems like a good idea to try and clean up
        closed panels. In particular, the Taurus polling thread can
        become pretty bogged down."""
        if self.registry:
            with self.registry.lock:
                print "cleaning up panel for", w.getModel(), "..."
                self._panels.pop(str(w.getModel()).lower(), None)
                w.setModel(None)
                print "done!"

    # Note: the tooltip stuff is broken and not currently in use.
    # Currently there is only the default tooltip which displays the
    # model name.

    def on_tooltip(self, models):
        # FIXME: looks like tooltip listeners aren't cleaned up until a new
        # tooltip is displayed. This seems wasteful.
        if models:
            all_models = []
            for model in models:
                if self.registry.device_validator.isValid(model):
                    all_models.append(model + "/State")
                    all_models.append(model + "/Status")
                else:
                    all_models.append(model)
            try:
                self.tooltip_registry.subscribe(all_models)
            except ValueError:
                pass
            self._tooltip_data = dict((str(model), {}) for model in models)
        else:
            self.tooltip_registry.subscribe()
            self._tooltip_data.clear()

    def _tooltip_updater(self, model, attr_value):
        value = attr_value.value
        device, attr = model.rsplit("/", 1)
        if attr in ("State", "Status"):
            if attr == "Status":
                # hack to keep newlines
                value = value.replace("\n", "<br>")
            self._tooltip_data.setdefault(device, {})[attr] = value
            #dev = evt_src.getParentObj()
            #info = dev.getHWObj().info()
            # self._tooltip_data[device]["Class"] = info.dev_class
            self.tooltip_trigger.emit(device)
        else:
            self._tooltip_data[model] = value

    def _update_device_tooltip(self, device):
        # TODO: redo this in a neat way.
        data = {"Class": "...", "State": "...", "Status": "..."}
        data.update(self._tooltip_data[device])
        html = json.dumps(
            ('<div>Class:&nbsp;<span>{Class}</span></div>' +
             '<div>State:&nbsp;<span class="{State}">{State}</span></div>' +
             '<div>Status:&nbsp;<span>{Status}</span></div>').format(**data))
        self.js.evaluate('synoptic.setTooltipHTML("%s", %s)' %
                         (device, html))

    def closeEvent(self, event):
        # Get rid of all opened panels, otherwise the application will
        # not exit cleanly.
        super(TaurusSynopticWidget, self).closeEvent(event)
        for model, panel in self._panels.items():
            print "closing panel for", model
            panel.close()


if __name__ == '__main__':
    import sys
    print sys.argv[1]
    # qapp = Qt.QApplication([])
    app = TaurusApplication()
    sw = TaurusSynopticWidget()
    app.focusChanged.connect(sw.onFocus)
    sw.setModel(sys.argv[1])
    sw.show()
    app.exec_()
