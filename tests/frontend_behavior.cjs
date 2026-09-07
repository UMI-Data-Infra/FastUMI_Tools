const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8').split('\nbind();')[0];
function app(preferences = {}) {
  const elements = new Map();
  const node = id => {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', textContent: '', value: '', checked: false, disabled: false,
      dataset: {}, options: [], classList: { toggle() {}, remove() {}, contains() { return false; } },
      setAttribute() {}, removeAttribute() {}, querySelectorAll() { return []; }
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    localStorage: { getItem: key => preferences[key] ?? null },
    matchMedia: () => ({ matches: false }),
    document: { getElementById: node, querySelector: () => node('sidebar'), querySelectorAll: () => [], activeElement: null },
  });
  vm.runInContext(source, context);
  return { node, run: code => vm.runInContext(code, context) };
}
const ui = app();
assert.equal(ui.run('state.locale'), 'zh-CN');
assert.equal(ui.run('state.theme'), 'light');
const saved = app({ 'fastumi-locale': 'en', 'fastumi-theme': 'system', 'fastumi-camera-generation': 'gen2' });
assert.equal(saved.run('state.locale'), 'en');
assert.equal(saved.run('state.theme'), 'system');
assert.equal(saved.run('state.cameraGeneration'), 'gen2');
ui.run(`
state.serviceConnected=true;
state.status={healthy:true,host:{supported:true,hostname:'test-host'},sdk:{installed:true},ros:{online:false},warning_codes:[],devices:[
{serial:'first',product:'Camera One',usb_generation:'USB 3.x'},
{serial:'second',product:'Camera Two',usb_generation:'USB 2.0'}]};
renderOverview();`);
assert.equal(ui.run('state.selectedSerial'), 'first');
ui.run('state.selectedSerial="second"; renderOverview()');
assert.match(ui.node('device-grid').innerHTML, /Camera Two/);
assert.doesNotMatch(ui.node('device-grid').innerHTML, /Camera One/);
ui.run('state.status.devices.reverse(); renderOverview()');
assert.equal(ui.run('state.selectedSerial'), 'second', 'selection survives device reordering');
ui.run('state.status.devices=state.status.devices.filter(d=>d.serial!=="second");renderOverview()');
assert.equal(ui.run('state.selectedSerial'), 'first', 'hot unplug selects a remaining connected device');
ui.run('state.status.devices=[];renderOverview()');
assert.equal(ui.run('state.selectedSerial'), null);
assert.match(ui.node('device-grid').innerHTML, /未检测到相机/);
assert.equal(ui.node('device-count').textContent, '0');
ui.run('state.status.devices=[{serial:"a\\\"<script>",product:"<img src=x onerror=alert(1)>"}];renderOverview()');
assert.doesNotMatch(ui.node('device-grid').innerHTML, /<img/);
assert.doesNotMatch(ui.node('device-list').innerHTML, /<script>/);
assert.match(ui.node('device-list').innerHTML, /&lt;script&gt;/);
// A selected SDK cannot bypass service-disconnection or unsupported-host gating.
ui.run('state.catalog={sdk:[{id:"sdk",available:true,compatible:true}],firmware:[{id:"fw",available:true,compatible:true}]};state.selectedSdk="sdk";state.selectedFirmware="fw";state.operation={status:"idle"}');
ui.node('firmware-device').value='first';
ui.run('updateResourceButtons()');
assert.equal(ui.node('install-sdk').disabled, false);
assert.equal(ui.node('preflight-firmware').disabled, false);
assert.equal(ui.node('flash-firmware').disabled, true);
ui.node('firmware-ack').checked=true;
ui.run('updateResourceButtons()');
assert.equal(ui.node('flash-firmware').disabled, false);
ui.run('state.serviceConnected=false;updateResourceButtons()');
for (const id of ['install-sdk','install-ros','preflight-firmware','flash-firmware']) assert.equal(ui.node(id).disabled,true,id);
ui.run('state.serviceConnected=true;state.cameraGeneration="gen2";updateResourceButtons()');
assert.equal(ui.node('firmware-panel').hidden,true);
assert.equal(ui.node('flash-firmware').disabled,true);
ui.run('state.cameraGeneration="gen1";state.status.host.supported=false;updateResourceButtons()');
assert.equal(ui.node('install-sdk').disabled,true);
assert.equal(ui.node('preflight-firmware').disabled,true);
ui.run('state.serviceConnected=false;state.operation.preview_running=true;renderMonitor();renderCamera()');
for (const id of ['start-ros-driver','stop-ros-driver','start-preview','stop-preview','restore-camera','start-calibration']) assert.equal(ui.node(id).disabled,true,id);
console.log('PASS: preferences, device selection, hot unplug, escaping, service and firmware gates');
