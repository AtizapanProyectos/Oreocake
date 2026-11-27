const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function agendador() {
    const elements = new Map();
    function element(id) {
        if (!elements.has(id)) elements.set(id, {
            value: '', textContent: '', innerHTML: '', style: {}, dataset: {}, checked: false,
            classList: {add() {}, remove() {}, toggle() {}},
            listeners: {}, addEventListener(event, callback) {this.listeners[event] = callback;},
            querySelector() {return element(id + '-input');}, contains() {return false;},
        });
        return elements.get(id);
    }
    const programas = ['tectum', 'beneficios'].map(value => Object.assign(element(value), {value}));
    const modalidades = ['En línea', 'Presencial'].map(value => Object.assign(element(value), {dataset: {modalidad: value}}));
    const ready = [];
    const document = {
        getElementById: element, querySelector: element,
        querySelectorAll(selector) {
            if (selector === 'input[name="programa"]') return programas;
            if (selector === '.hpa-modalidad-btn') return modalidades;
            return [];
        },
        addEventListener(event, callback) {if (event === 'DOMContentLoaded') ready.push(callback);},
    };
    const window = {document, URLSearchParams, FormData, console, setTimeout, clearTimeout};
    window.window = window;
    let code = fs.readFileSync('templates/admin_agendar_cita.html', 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
    code = code.replace(/\{\{[\s\S]*?\}\}/g, '{}').replace(/\{%[\s\S]*?%\}/g, 'endpoint');
    const end = code.lastIndexOf('});');
    code = code.slice(0, end) + `window.test = {elegirPaciente, aplicarModalidad,
        estado: () => ({pacienteId, modalidad, esTectumSeleccionado, esBeneficiosSeleccionado,
                       fechaSeleccionada, horaSeleccionada}),
        horario: () => {fechaSeleccionada = '2026-12-01'; horaSeleccionada = '10:00 AM';}};` + code.slice(end);
    const requests = [];
    window.fetch = (url, options) => {
            requests.push({url, options});
            return Promise.resolve({json: async () => ({status: 'success', dias: {}, total: 110})});
        };
    vm.runInNewContext(code, window);
    ready.forEach(callback => callback());
    return {window, element, programas, modalidades, requests};
}

test('precarga modalidad y Beneficios, permite cambiar y limpia al cambiar paciente', () => {
    const {window, element, programas, modalidades} = agendador();
    window.test.elegirPaciente({id: 1, nombre: 'Consultante', es_beneficios: true, modalidad_predeterminada: 'Presencial'});
    assert.equal(window.test.estado().modalidad, 'Presencial');
    assert.equal(programas[1].checked, true);
    modalidades[0].listeners.change();
    assert.equal(window.test.estado().modalidad, 'En línea');
    window.cambiarConvenioAdmin('tectum');
    assert.equal(programas[0].checked, true);
    assert.equal(programas[1].checked, false);
    window.test.horario();
    element('hpa-cambiar-paciente').listeners.click();
    const state = window.test.estado();
    assert.equal(state.pacienteId, null);
    assert.equal(state.fechaSeleccionada, null);
    assert.equal(state.horaSeleccionada, null);
    assert.equal(state.esTectumSeleccionado, false);
    assert.equal(state.esBeneficiosSeleccionado, false);
});

test('envía ambas banderas explícitas y la modalidad seleccionada', () => {
    const {window, element, requests} = agendador();
    window.test.elegirPaciente({id: 1, nombre: 'Consultante', es_beneficios: true, modalidad_predeterminada: 'Presencial'});
    window.test.aplicarModalidad('En línea');
    window.test.horario();
    element('hpa-form').listeners.submit({preventDefault() {}});
    const body = requests.find(r => r.options?.method === 'POST').options.body;
    assert.equal(body.get('es_beneficios'), '1');
    assert.equal(body.get('es_tectum'), '0');
    assert.equal(body.get('modalidad'), 'En línea');
});
