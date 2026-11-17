const { createApp, reactive, ref, onMounted, onBeforeUnmount, watch, computed } = Vue;
const { createRouter, createWebHistory } = VueRouter;

/* ---------------- Global State & Helpers ---------------- */
const state = reactive({
  user: JSON.parse(localStorage.getItem('user') || 'null'),
  toast: null,
  notifs: [],
  confirm: null,
});

const token = () => localStorage.getItem('token');

// First name for greetings: "Dr. Rajesh Kumar" -> "Rajesh" (a leading title is not a name).
const firstNameOf = (name) => String(name || '').trim().replace(/^(dr|prof|mr|mrs|ms|miss)\.?\s+/i, '').split(/\s+/)[0] || 'there';

function toast(msg, type = 'success') {
  state.toast = { msg, type };
  setTimeout(() => {
    if (state.toast && state.toast.msg === msg) state.toast = null;
  }, 4000);
}

// One confirmation dialog for the whole app (rendered once in App): await showConfirm('Delete X?', { confirmText: 'Delete' })
function showConfirm(message, opts = {}) {
  return new Promise((resolve) => { state.confirm = { message, resolve, ...opts }; });
}

let loggingOut = false;
async function logout() {
  if (loggingOut) return; // api() calls logout() on 401/422, so guard against re-entry
  loggingOut = true;
  try {
    if (token()) await api('POST', '/auth/logout');
  } catch (e) { /* token already invalid: nothing to revoke */ }
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  state.user = null;
  loggingOut = false;
  router.push('/login');
}

async function api(method, url, data) {
  const headers = { 'Content-Type': 'application/json' };
  if (token()) headers.Authorization = 'Bearer ' + token();
  const r = await fetch('/api' + url, {
    method,
    headers,
    body: data ? JSON.stringify(data) : undefined,
  });
  let j = {};
  try { j = await r.json(); } catch (e) { /* empty body */ }
  if (!r.ok) {
    // 401 = expired/revoked token or an account that was deactivated (the body uses "msg" for token errors and
    // "error" for the account check); 422 = malformed token. A wrong password at sign-in has no token yet.
    if (token() && (r.status === 401 || (r.status === 422 && j.msg))) logout();
    const err = new Error(j.error || j.msg || 'Request failed');
    err.status = r.status;
    err.data = j;
    throw err;
  }
  return j;
}

async function act(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg, 'success');
    return r === undefined ? true : r;
  } catch (e) {
    toast(e.message, 'danger');
    return false;
  }
}

// Modern harmonious color generator for exam cards
const cardColors = [
  'linear-gradient(135deg, #4f46e5 0%, #3730a3 100%)', // Indigo
  'linear-gradient(135deg, #0d9488 0%, #115e59 100%)', // Teal
  'linear-gradient(135deg, #2563eb 0%, #1e40af 100%)', // Blue
  'linear-gradient(135deg, #7c3aed 0%, #5b21b6 100%)', // Purple
  'linear-gradient(135deg, #0284c7 0%, #0369a1 100%)', // Sky
  'linear-gradient(135deg, #d97706 0%, #b45309 100%)', // Amber
];
const cardColor = (id) => cardColors[(id || 0) % cardColors.length];

const fmtDay = (iso) => {
  if (!iso) return { d: '--', m: '---' };
  const d = new Date(iso + 'T00:00:00');
  return { d: d.getDate(), m: d.toLocaleString('en', { month: 'short' }) };
};

const fmtDate = (iso) => {
  if (!iso) return '-';
  const d = new Date(iso + 'T00:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((d - today) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  return d.toLocaleDateString('en', { day: 'numeric', month: 'short', year: 'numeric' });
};

// Soft badge style mapper
const badgeClass = (s) => {
  const map = {
    Available: 'badge-soft-success',
    Active: 'badge-soft-success',
    Booked: 'badge-soft-primary',
    'Booking Open': 'badge-soft-success',
    'Slot Creation': 'badge-soft-info',
    Completed: 'badge-soft-secondary',
    Full: 'badge-soft-warning',
    Cancelled: 'badge-soft-danger',
    Closed: 'badge-soft-dark',
    Draft: 'badge-soft-secondary',
    Inactive: 'badge-soft-danger',
    Pending: 'badge-soft-warning',
    Done: 'badge-soft-success',
    Failed: 'badge-soft-danger',
  };
  return map[s] || 'badge-soft-secondary';
};

/* ---------------- Shared UI Components ---------------- */
// Dialog basics shared by Modal and ConfirmModal: focus moves into the dialog and Escape closes it.
// (Options API hooks on purpose: Vue 3 ignores setup() declared inside a mixin.)
const dialogFocus = {
  mounted() {
    this._opener = document.activeElement;
    if (this.$refs.el) {
      this.$refs.el.focus();
      this._trap = (e) => {
        if (e.key !== 'Tab') return;
        const nodes = this.$refs.el.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (!nodes.length) return;
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          last.focus();
          e.preventDefault();
        } else if (!e.shiftKey && document.activeElement === last) {
          first.focus();
          e.preventDefault();
        }
      };
      this.$refs.el.addEventListener('keydown', this._trap);
    }
  },
  beforeUnmount() {
    if (this._trap && this.$refs.el) this.$refs.el.removeEventListener('keydown', this._trap);
    if (this._opener && this._opener.focus) this._opener.focus();
  },
};

const Modal = {
  mixins: [dialogFocus],
  props: ['title', 'wide', 'icon'],
  emits: ['close'],
  template: `
  <div ref="el" tabindex="-1" role="dialog" aria-modal="true" :aria-label="title" class="modal-backdrop-custom"
       @click.self="$emit('close')" @keydown.esc="$emit('close')">
    <div class="modal-dialog" :class="wide ? 'modal-lg' : ''">
      <div class="modal-content printable">
        <div class="modal-header">
          <h5 class="modal-title d-flex align-items-center gap-2">
            <i v-if="icon" :class="'bi ' + icon + ' text-primary'"></i>
            <span>{{ title }}</span>
          </h5>
          <button type="button" class="btn-close ms-auto no-print" aria-label="Close" @click="$emit('close')"></button>
        </div>
        <div class="modal-body"><slot /></div>
      </div>
    </div>
  </div>`,
};

const FormModal = {
  props: ['title', 'fields', 'initial', 'submit', 'icon'],
  emits: ['close'],
  components: { Modal },
  data() {
    return { f: { ...(this.initial || {}) }, error: '', saving: false };
  },
  methods: {
    async save() {
      this.saving = true;
      this.error = '';
      try {
        await this.submit(this.f);
        this.$emit('close');
      } catch (e) {
        this.error = e.message;
      } finally {
        this.saving = false;
      }
    },
  },
  template: `
  <Modal :title="title" :icon="icon || 'bi-pencil-square'" @close="$emit('close')">
    <form @submit.prevent="save">
      <div v-for="fl in fields" :key="fl.key" class="mb-3">
        <label class="form-label small fw-semibold text-muted mb-1">
          {{ fl.label }}<span v-if="fl.required" class="text-danger"> *</span>
        </label>
        
        <select v-if="fl.type === 'select'" class="form-select" v-model="f[fl.key]" :required="fl.required">
          <option v-if="!fl.required" value="">-- Select --</option>
          <option v-for="o in fl.options" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        
        <textarea v-else-if="fl.type === 'textarea'" class="form-control" rows="3" v-model="f[fl.key]" :placeholder="fl.placeholder || ''"></textarea>
        
        <input v-else class="form-control" :type="fl.type || 'text'" v-model="f[fl.key]" :required="fl.required"
               :min="fl.min" :step="fl.step" :minlength="fl.minlength" :maxlength="fl.maxlength" :pattern="fl.pattern"
               :placeholder="fl.placeholder || ''">
      </div>
      
      <div v-if="error" class="alert alert-danger py-2 small d-flex align-items-center gap-2">
        <i class="bi bi-exclamation-octagon-fill"></i>
        <span>{{ error }}</span>
      </div>
      
      <div class="d-flex justify-content-end gap-2 pt-2">
        <button type="button" class="btn btn-light" @click="$emit('close')">Cancel</button>
        <button class="btn btn-primary" :disabled="saving">
          <span v-if="saving" class="spinner-border spinner-border-sm me-1"></span>
          <i v-else class="bi bi-check2"></i>
          <span>Save</span>
        </button>
      </div>
    </form>
  </Modal>`,
};

const ConfirmModal = {
  mixins: [dialogFocus],
  props: ['title', 'message', 'confirmText', 'cancelText', 'danger'],
  emits: ['confirm', 'cancel'],
  template: `
  <div ref="el" tabindex="-1" role="alertdialog" aria-modal="true" :aria-label="title || 'Please confirm'"
       class="modal-backdrop-custom" @click.self="$emit('cancel')" @keydown.esc="$emit('cancel')">
    <div class="modal-dialog modal-sm">
      <div class="modal-content">
        <div class="modal-header py-3">
          <h6 class="modal-title fw-bold d-flex align-items-center gap-2">
            <i :class="danger ? 'bi bi-exclamation-triangle-fill text-danger' : 'bi bi-info-circle-fill text-primary'"></i>
            <span>{{ title || 'Please Confirm' }}</span>
          </h6>
          <button type="button" class="btn-close ms-auto" aria-label="Close" @click="$emit('cancel')"></button>
        </div>
        <div class="modal-body py-3 text-secondary">
          {{ message }}
        </div>
        <div class="modal-footer py-2">
          <button class="btn btn-light btn-sm" @click="$emit('cancel')">{{ cancelText || 'Cancel' }}</button>
          <button class="btn btn-sm" :class="danger ? 'btn-danger' : 'btn-primary'" @click="$emit('confirm')">
            {{ confirmText || 'Confirm' }}
          </button>
        </div>
      </div>
    </div>
  </div>`,
};

// Bar / line chart drawn as plain SVG (no charting library). Same props as before: type ('bar' | 'line'), labels, values,
// title, accentColor. Whole-number axis, hover tooltips (<title>), screen-reader summary, "No data yet" when empty.
const ChartBox = {
  props: ['type', 'labels', 'values', 'title', 'accentColor'],
  setup(props) {
    const W = 360, H = 232, L = 34, R = 10, T = 22;          // the SVG scales to its container (viewBox)
    const isLine = computed(() => props.type === 'line');
    const color = computed(() => props.accentColor || '#4f46e5');
    const nums = computed(() => (props.values || []).map((v) => Number(v) || 0));
    const names = computed(() => (props.labels || []).map((s) => String(s)));
    const rotate = computed(() => !isLine.value && nums.value.length > 2);   // long exam names: slanted labels
    const plot = computed(() => {
      const bottom = rotate.value ? 64 : 34;                                   // room under the plot for the labels
      return { x: L, y: T, w: W - L - R, h: H - T - bottom };
    });
    const ticks = computed(() => {                                              // round whole numbers, about 4-5 gridlines
      const max = Math.max(1, ...nums.value);
      const raw = max / 4, pow = Math.pow(10, Math.floor(Math.log10(raw)));
      const step = Math.max(1, [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw));   // 1, 2, 5, 10, 20, 50, 100 ...
      const top = step * Math.ceil(max / step);
      const list = [];
      for (let v = 0; v <= top; v += step) list.push(v);
      return { list, top };
    });
    const yOf = (v) => plot.value.y + plot.value.h - (v / ticks.value.top) * plot.value.h;
    const short = (s, n = 14) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
    const pretty = (s) => {                                                     // 2026-11-08 -> Nov 8
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
      return m ? new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : s;
    };

    const bars = computed(() => {
      if (isLine.value || !nums.value.length) return [];
      const p = plot.value, band = p.w / nums.value.length, w = Math.min(band * 0.62, 46);
      return nums.value.map((v, i) => {
        const x = p.x + band * i + (band - w) / 2, y = yOf(v), h = p.y + p.h - y, r = Math.min(5, w / 2, h);
        return {
          d: h > 0 ? `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z` : '',
          cx: x + w / 2, valueY: y - 5, v, text: short(names.value[i] || ''), tip: (names.value[i] || '') + ': ' + v,
        };
      });
    });

    const line = computed(() => {
      const n = nums.value.length, p = plot.value;
      if (!isLine.value || !n) return null;
      const pts = nums.value.map((v, i) => ({
        x: n === 1 ? p.x + p.w / 2 : p.x + (p.w * i) / (n - 1), y: yOf(v), v,
        text: pretty(names.value[i] || ''), tip: pretty(names.value[i] || '') + ': ' + v,
      }));
      const path = pts.map((q, i) => (i ? 'L' : 'M') + q.x + ',' + q.y).join(' ');
      const area = n > 1 ? path + ` L${pts[n - 1].x},${p.y + p.h} L${pts[0].x},${p.y + p.h} Z` : '';
      const every = Math.max(1, Math.ceil(n / 5));                                // about five date labels
      const shown = new Set();
      for (let i = 0; i < n; i += every) shown.add(i);
      const last = Math.max(...shown);                                            // always label the newest point,
      if (n - 1 - last >= every / 2) shown.add(n - 1);                            // unless it would crowd the previous label
      else if (last !== n - 1) { shown.delete(last); shown.add(n - 1); }
      pts.forEach((q, i) => { q.showLabel = shown.has(i); });
      return { pts, path, area, dots: n <= 31 };
    });

    const summary = computed(() => (props.title || 'Chart') + ': ' + (nums.value.length
      ? nums.value.map((v, i) => (names.value[i] || '') + ' ' + v).join(', ') : 'no data'));

    return { W, H, plot, ticks, yOf, color, isLine, rotate, nums, bars, line, summary };
  },
  template: `
  <div class="chart-box">
    <div class="chart-title">{{ title }}</div>
    <div v-if="!nums.length" class="chart-empty">No data yet</div>
    <svg v-else class="chart-svg" :viewBox="'0 0 ' + W + ' ' + H" role="img" :aria-label="summary">
      <g class="chart-grid" aria-hidden="true">
        <g v-for="t in ticks.list" :key="t">
          <line :x1="plot.x" :x2="plot.x + plot.w" :y1="yOf(t)" :y2="yOf(t)" />
          <text :x="plot.x - 6" :y="yOf(t) + 3.5" text-anchor="end">{{ t }}</text>
        </g>
      </g>

      <g v-if="!isLine" aria-hidden="true">
        <g v-for="(b, i) in bars" :key="i" class="chart-bar">
          <path v-if="b.d" :d="b.d" :fill="color" fill-opacity="0.82"><title>{{ b.tip }}</title></path>
          <text class="chart-value" :x="b.cx" :y="b.valueY" text-anchor="middle">{{ b.v }}</text>
          <text v-if="rotate" class="chart-label" text-anchor="end" :transform="'translate(' + (b.cx + 4) + ',' + (plot.y + plot.h + 13) + ') rotate(-32)'">{{ b.text }}</text>
          <text v-else class="chart-label" text-anchor="middle" :x="b.cx" :y="plot.y + plot.h + 16">{{ b.text }}</text>
        </g>
      </g>

      <g v-else-if="line" aria-hidden="true">
        <path v-if="line.area" :d="line.area" :fill="color" fill-opacity="0.12" />
        <path v-if="line.pts.length > 1" :d="line.path" fill="none" :stroke="color" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
        <g v-for="(q, i) in line.pts" :key="i">
          <circle v-if="line.dots || q.showLabel" :cx="q.x" :cy="q.y" r="3.6" fill="#fff" :stroke="color" stroke-width="2"><title>{{ q.tip }}</title></circle>
          <text v-if="q.showLabel" class="chart-label" text-anchor="middle" :x="q.x" :y="plot.y + plot.h + 16">{{ q.text }}</text>
        </g>
      </g>
    </svg>
  </div>`,
};

/* ---------------- Shared page building blocks (every page is made from these) ---------------- */
// Single source of truth for page titles: the sidebar, the topbar breadcrumb and the page header all read this.
const pageMeta = {
  '/admin': { title: 'Dashboard' },
  '/admin/courses': { title: 'Courses', sub: 'Manage course codes and descriptions.' },
  '/admin/exams': { title: 'Examinations', sub: 'Configure timelines, rubrics and examiners, and publish results.' },
  '/admin/examiners': { title: 'Examiners', sub: 'Manage examiner accounts, departments and status.' },
  '/admin/students': { title: 'Students', sub: 'View registered students and manage their access.' },
  '/admin/slots': { title: 'Slots & Bookings', sub: 'Monitor examiner slots and reschedule student bookings.' },
  '/admin/search': { title: 'Search', sub: 'Find students, examiners, examinations and bookings.' },
  '/examiner': { title: 'Dashboard' },
  '/examiner/slots': { title: 'My Slots', sub: 'Create and manage your availability slots during the Slot Creation period.' },
  '/examiner/evaluate': { title: 'Evaluate Students', sub: 'Score students against the examination rubric.' },
  '/student': { title: 'Dashboard' },
  '/student/exams': { title: 'Examinations', sub: 'Browse examinations, review the rubric and book a slot.' },
  '/student/bookings': { title: 'My Bookings', sub: 'Track your booked slots and meeting links.' },
  '/student/results': { title: 'Results', sub: 'Published scores, rubric breakdowns and examiner feedback.' },
  '/profile': { title: 'Profile', sub: 'Update your contact details and password.' },
};

const PageHeader = {
  props: ['title', 'subtitle'],
  setup() {
    const route = VueRouter.useRoute();
    return { meta: computed(() => pageMeta[route.path] || {}) };
  },
  template: `
  <div class="page-header">
    <div>
      <h1>{{ title || meta.title }}</h1>
      <div class="page-sub" v-if="subtitle || meta.sub">{{ subtitle || meta.sub }}</div>
    </div>
    <div class="page-actions"><slot /></div>
  </div>`,
};

const HeroBanner = {
  props: ['title', 'subtitle'],
  template: `
  <div class="hero-banner mb-4 d-flex flex-wrap justify-content-between align-items-center gap-3">
    <div>
      <h1>{{ title }}</h1>
      <div class="opacity-90">{{ subtitle }}</div>
    </div>
    <div class="hero-actions"><slot /></div>
  </div>`,
};

const LoadingBlock = {
  template: `<div class="text-center py-5" role="status" aria-live="polite"><div class="spinner-border text-primary"></div><span class="visually-hidden">Loading...</span></div>`,
};

const EmptyRow = {
  props: { colspan: { type: Number, default: 1 }, icon: { type: String, default: 'bi-inbox' }, text: String, compact: Boolean },
  template: `
  <tr><td :colspan="colspan" class="empty-state" :class="{ compact }">
    <div class="empty-icon"><i :class="'bi ' + icon"></i></div>
    <div>{{ text }}</div>
  </td></tr>`,
};

const FilterBar = {
  props: ['modelValue', 'placeholder', 'autofocus'],
  emits: ['update:modelValue'],
  mounted() { if (this.autofocus && this.$refs.input) this.$refs.input.focus(); },
  template: `
  <div class="card filter-bar">
    <div v-if="placeholder" class="input-group filter-search">
      <span class="input-group-text bg-white text-muted"><i class="bi bi-search"></i></span>
      <input ref="input" class="form-control border-start-0" :placeholder="placeholder" :aria-label="placeholder"
             :value="modelValue" @input="$emit('update:modelValue', $event.target.value)">
    </div>
    <slot />
  </div>`,
};

const StatCard = {
  props: ['label', 'value', 'icon', 'tone'],
  template: `
  <div class="card-stat" :class="'tone-' + (tone || 'blue')">
    <div class="stat-icon"><i :class="'bi ' + icon"></i></div>
    <div class="stat-label">{{ label }}</div>
    <div class="stat-value">{{ value ?? '-' }}</div>
  </div>`,
};

const StatusBadge = {
  props: ['status'],
  methods: { badgeClass },
  template: `<span class="badge-soft" :class="badgeClass(status)">{{ status }}</span>`,
};

const SeatMeter = {
  props: ['booked', 'capacity'],
  template: `
  <div class="seat-meter">
    <div class="progress" role="progressbar" :aria-valuenow="booked" aria-valuemin="0" :aria-valuemax="capacity">
      <div class="progress-bar" :style="{ width: (capacity ? (booked / capacity) * 100 : 0) + '%' }"></div>
    </div>
    <span class="small fw-semibold">{{ booked }}/{{ capacity }}</span>
  </div>`,
};

/* ---------------- Authentication Pages ---------------- */
const Login = {
  setup() {
    // The person says who they are signing in as; the server checks the account really has that role.
    // The last choice is remembered on this browser (convenience only; nothing is pre-selected on a first visit).
    let lastRole = '';
    try { lastRole = localStorage.getItem('lastRole') || ''; } catch (e) { /* storage unavailable */ }
    const roles = [
      { value: 'admin', label: 'Admin', icon: 'bi-shield-lock' },
      { value: 'examiner', label: 'Examiner', icon: 'bi-person-badge' },
      { value: 'student', label: 'Student', icon: 'bi-mortarboard' },
    ];
    const f = reactive({ role: roles.some((r) => r.value === lastRole) ? lastRole : '', email: '', password: '' });
    const showPass = ref(false);
    const loading = ref(false);
    const roleMissing = ref(false);

    const submit = async () => {
      if (!f.role) { roleMissing.value = true; return; }
      roleMissing.value = false;
      loading.value = true;
      try {
        const r = await act(() => api('POST', '/auth/login', f));
        if (!r) return;
        try { localStorage.setItem('lastRole', f.role); } catch (e) { /* ignore */ }
        localStorage.setItem('token', r.token);
        localStorage.setItem('user', JSON.stringify(r.user));
        state.user = r.user;
        router.push('/' + r.user.role);
      } finally {
        loading.value = false;
      }
    };

    // Demo shortcuts exist only when the server was started with EMP_DEMO_LOGIN=1 (it then adds them to the page).
    // Nothing is hard-coded in this file, so a normal deployment exposes no credentials.
    let demo = [];
    try { demo = JSON.parse(document.querySelector('meta[name="emp-demo"]').content); } catch (e) { /* demo mode off */ }
    const demoIcon = { Admin: 'bi-shield-lock', Examiner: 'bi-person-badge', Student: 'bi-mortarboard' };

    const quickFill = (a) => {
      f.role = a.role.toLowerCase();
      f.email = a.email;
      f.password = a.password;
      submit();
    };

    return { f, roles, roleMissing, showPass, loading, submit, quickFill, demo, demoIcon };
  },
  template: `
  <div class="auth-bg">
    <div class="auth-card">
      <div class="auth-card-body">
        <div class="text-center mb-4">
          <div class="d-inline-flex p-3 rounded-circle bg-primary-subtle text-primary mb-3">
            <i class="bi bi-mortarboard-fill fs-2"></i>
          </div>
          <h4 class="fw-bold mb-1">Welcome to EMP</h4>
          <p class="text-muted small">Examination Management Portal &middot; V2</p>
        </div>

        <!-- Quick Demo Switcher (only when the server runs with EMP_DEMO_LOGIN=1) -->
        <div v-if="demo.length" class="p-3 mb-4 rounded-3 bg-light border">
          <div class="small fw-semibold text-muted text-uppercase mb-2" style="font-size:0.7rem; letter-spacing:0.05em">
            <i class="bi bi-lightning-charge-fill text-warning me-1"></i>One-Click Demo Login
          </div>
          <div class="d-flex flex-wrap gap-1">
            <button v-for="a in demo" :key="a.role" type="button" class="demo-role-btn flex-fill" @click="quickFill(a)">
              <i class="bi me-1" :class="demoIcon[a.role]"></i>{{ a.role }}
            </button>
          </div>
        </div>

        <form @submit.prevent="submit">
          <div class="mb-3" role="radiogroup" aria-labelledby="role-label">
            <div id="role-label" class="form-label small fw-semibold text-muted">Sign in as</div>
            <div class="role-choice">
              <label v-for="r in roles" :key="r.value" class="role-option" :class="{ active: f.role === r.value }">
                <input type="radio" name="role" class="visually-hidden" :value="r.value" v-model="f.role" @change="roleMissing = false">
                <i class="bi" :class="r.icon"></i>
                <span>{{ r.label }}</span>
              </label>
            </div>
            <div v-if="roleMissing" class="text-danger small mt-1" role="alert">Choose how you are signing in: Admin, Examiner or Student.</div>
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">Email address</label>
            <div class="input-group">
              <span class="input-group-text bg-light text-muted border-end-0"><i class="bi bi-envelope"></i></span>
              <input type="email" class="form-control border-start-0" v-model="f.email" placeholder="name@domain.edu" required>
            </div>
          </div>

          <div class="mb-4">
            <label class="form-label small fw-semibold text-muted">Password</label>
            <div class="input-group">
              <span class="input-group-text bg-light text-muted border-end-0"><i class="bi bi-lock"></i></span>
              <input :type="showPass ? 'text' : 'password'" class="form-control border-start-0 border-end-0" v-model="f.password" placeholder="••••••••" required>
              <button class="btn btn-light border border-start-0" type="button" @click="showPass = !showPass">
                <i :class="showPass ? 'bi bi-eye-slash text-muted' : 'bi bi-eye text-muted'"></i>
              </button>
            </div>
          </div>

          <button class="btn btn-primary w-100 py-2 mb-3" :disabled="loading">
            <span v-if="loading" class="spinner-border spinner-border-sm me-1"></span>
            <i v-else class="bi bi-box-arrow-in-right"></i>
            <span>Sign In</span>
          </button>
        </form>

        <div class="text-center small text-muted">
          New student? <router-link to="/register" class="fw-semibold">Create account</router-link>
        </div>
      </div>
    </div>
  </div>`,
};

const Register = {
  setup() {
    const f = reactive({ name: '', email: '', password: '', phone: '', department: '' });
    const loading = ref(false);
    const submit = async () => {
      loading.value = true;
      try {
        if (await act(() => api('POST', '/auth/register', f), 'Registration successful. Please sign in.')) {
          router.push('/login');
        }
      } finally {
        loading.value = false;
      }
    };
    return { f, loading, submit };
  },
  template: `
  <div class="auth-bg">
    <div class="auth-card" style="max-width: 500px;">
      <div class="auth-card-body">
        <div class="text-center mb-4">
          <div class="d-inline-flex p-3 rounded-circle bg-primary-subtle text-primary mb-3">
            <i class="bi bi-person-plus-fill fs-2"></i>
          </div>
          <h4 class="fw-bold mb-1">Student Registration</h4>
          <p class="text-muted small">Register to book viva & examination slots</p>
        </div>

        <form @submit.prevent="submit">
          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">Full Name <span class="text-danger">*</span></label>
            <input class="form-control" v-model="f.name" placeholder="e.g. John Doe" required maxlength="100">
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">Institutional Email <span class="text-danger">*</span></label>
            <input type="email" class="form-control" v-model="f.email" placeholder="john@student.edu" required>
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">Password (min 6 characters) <span class="text-danger">*</span></label>
            <input type="password" class="form-control" v-model="f.password" placeholder="••••••••" required minlength="6">
          </div>

          <div class="row g-2 mb-4">
            <div class="col-6">
              <label class="form-label small fw-semibold text-muted">Contact Phone</label>
              <input class="form-control" v-model="f.phone" placeholder="+91-98765..." pattern="[0-9+\\- ]{7,15}">
            </div>
            <div class="col-6">
              <label class="form-label small fw-semibold text-muted">Department</label>
              <input class="form-control" v-model="f.department" placeholder="Computer Science">
            </div>
          </div>

          <button class="btn btn-primary w-100 py-2 mb-3" :disabled="loading">
            <span v-if="loading" class="spinner-border spinner-border-sm me-1"></span>
            <i v-else class="bi bi-check2-circle"></i>
            <span>Complete Registration</span>
          </button>
        </form>

        <div class="text-center small text-muted">
          Already registered? <router-link to="/login" class="fw-semibold">Sign in</router-link>
        </div>
      </div>
    </div>
  </div>`,
};

/* ---------------- Admin Pages ---------------- */
const AdminDash = {
  components: { ChartBox },
  setup() {
    const s = ref({});
    const c = ref({ popular: [], workload: [], trend: [] });
    const rep = ref(null);
    const loading = ref(false);

    onMounted(async () => {
      loading.value = true;
      try {
        s.value = await api('GET', '/dashboard');
        c.value = await api('GET', '/admin/charts');
        rep.value = await api('GET', '/admin/report');
      } finally {
        loading.value = false;
      }
    });

    const sendReport = () => act(() => api('POST', '/admin/report/send'), 'Report email sent to admin.');
    const downloadPdf = async () => {
      const r = await fetch('/api/admin/report/pdf', { headers: { Authorization: 'Bearer ' + token() } });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await r.blob());
      a.download = 'examination_report.pdf';
      a.click();
    };

    const tiles = computed(() => [
      { label: 'Courses', val: s.value.courses, icon: 'bi-book-half', tone: 'indigo' },
      { label: 'Examinations', val: s.value.exams, icon: 'bi-journal-check', tone: 'blue' },
      { label: 'Students', val: s.value.students, icon: 'bi-mortarboard', tone: 'teal' },
      { label: 'Examiners', val: s.value.examiners, icon: 'bi-person-badge', tone: 'violet' },
      { label: 'Total Slots', val: s.value.slots, icon: 'bi-calendar3', tone: 'amber' },
      { label: 'Bookings', val: s.value.bookings, icon: 'bi-ticket-detailed', tone: 'rose' },
    ]);
    const firstName = computed(() => firstNameOf(state.user && state.user.name));

    return { tiles, c, rep, sendReport, downloadPdf, loading, firstName };
  },
  template: `
  <div>
    <HeroBanner :title="'Hello, ' + firstName + ' 👋'" subtitle="Monitor examinations, examiner workload and scheduling at a glance.">
      <template v-if="rep">
        <button class="btn btn-light" @click="downloadPdf"><i class="bi bi-file-earmark-pdf"></i> Download PDF</button>
        <button class="btn btn-light" @click="sendReport"><i class="bi bi-envelope"></i> Email Report</button>
      </template>
    </HeroBanner>

    <LoadingBlock v-if="loading" />

    <div v-else>
      <!-- Stat Tiles -->
      <div class="row g-3 mb-4">
        <div class="col-6 col-md-4 col-lg-2" v-for="t in tiles" :key="t.label">
          <StatCard :label="t.label" :value="t.val" :icon="t.icon" :tone="t.tone" />
        </div>
      </div>

      <!-- Charts Section -->
      <div class="row g-3 mb-4">
        <div class="col-md-4">
          <div class="card p-3 h-100">
            <ChartBox type="bar" :labels="c.popular.map(x => x.label)" :values="c.popular.map(x => x.value)"
                      title="Most Popular Examinations" accentColor="#4f46e5" />
          </div>
        </div>
        <div class="col-md-4">
          <div class="card p-3 h-100">
            <ChartBox type="bar" :labels="c.workload.map(x => x.label)" :values="c.workload.map(x => x.value)"
                      title="Examiner Evaluation Workload" accentColor="#0d9488" />
          </div>
        </div>
        <div class="col-md-4">
          <div class="card p-3 h-100">
            <ChartBox type="line" :labels="c.trend.map(x => x.label)" :values="c.trend.map(x => x.value)"
                      title="30-Day Booking Trends" accentColor="#2563eb" />
          </div>
        </div>
      </div>

      <!-- Monthly Report Summary Card -->
      <div class="card" v-if="rep">
        <div class="card-body p-4">
          <div class="d-flex justify-content-between align-items-center mb-3">
            <div class="d-flex align-items-center gap-2">
              <i class="bi bi-file-text-fill text-primary fs-4"></i>
              <h5 class="fw-bold mb-0">Monthly Report &middot; {{ rep.month }}</h5>
            </div>
            <span class="badge-soft badge-soft-primary">Official Academic Audit</span>
          </div>

          <div class="row g-3">
            <div class="col-md-3">
              <div class="panel-inset">
                <div class="text-muted small">Exams Conducted</div>
                <div class="fs-4 fw-bold text-dark">{{ rep.examinations_conducted }}</div>
              </div>
            </div>
            <div class="col-md-3">
              <div class="panel-inset">
                <div class="text-muted small">Students Evaluated</div>
                <div class="fs-4 fw-bold text-dark">{{ rep.students_evaluated }}</div>
              </div>
            </div>
            <div class="col-md-3">
              <div class="panel-inset">
                <div class="text-muted small">Completed Bookings</div>
                <div class="fs-4 fw-bold text-dark">{{ rep.completed_bookings }}</div>
              </div>
            </div>
            <div class="col-md-3">
              <div class="panel-inset">
                <div class="text-muted small">Pending Evaluations</div>
                <div class="fs-4 fw-bold" :class="rep.pending_evaluations ? 'text-danger' : 'text-success'">
                  {{ rep.pending_evaluations }}
                </div>
              </div>
            </div>
          </div>

          <div class="mt-3 pt-3 border-top small text-muted">
            <b>Most Active Examiners:</b>
            <span class="ms-1">{{ rep.active_examiners.map(e => e.name + ' (' + e.evaluations + ' evaluations)').join(' &middot; ') || 'No evaluations recorded this month.' }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>`,
};

const AdminCourses = {
  components: { FormModal },
  setup() {
    const rows = ref([]);
    const form = ref(null);
    const loading = ref(false);
    const load = async () => {
      loading.value = true;
      try { rows.value = await api('GET', '/courses'); } finally { loading.value = false; }
    };
    onMounted(load);

    const fields = [
      { key: 'code', label: 'Course Code (e.g. CS101)', required: true, maxlength: 20 },
      { key: 'name', label: 'Course Title', required: true },
      { key: 'description', label: 'Curriculum Description', type: 'textarea' },
      { key: 'status', label: 'Status', type: 'select', required: true, options: [{ value: 'Active', label: 'Active' }, { value: 'Inactive', label: 'Inactive' }] },
    ];

    const open = (c) => (form.value = { initial: c ? { ...c } : { status: 'Active' }, id: c && c.id });
    const submit = async (d) => {
      await (form.value.id ? api('PUT', '/courses/' + form.value.id, d) : api('POST', '/courses', d));
      toast(form.value.id ? 'Course updated' : 'Course created');
      load();
    };
    const del = async (c) => {
      if (await showConfirm('Are you sure you want to delete course ' + c.code + '? All related exams must be deleted first.', { confirmText: 'Delete' })) {
        if (await act(() => api('DELETE', '/courses/' + c.id), 'Course removed')) load();
      }
    };

    return { rows, form, fields, open, submit, del, badgeClass, loading };
  },
  template: `
  <div>
    <PageHeader>
      <button class="btn btn-primary" @click="open()">
        <i class="bi bi-plus-lg"></i> New Course
      </button>
    </PageHeader>

    <LoadingBlock v-if="loading" />

    <div v-else class="card">
      <div class="table-responsive">
        <table class="table table-hover mb-0 align-middle">
          <thead>
            <tr>
              <th>Course Code</th>
              <th>Course Name</th>
              <th>Description</th>
              <th>Status</th>
              <th class="text-end">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in rows" :key="c.id">
              <td><span class="fw-bold text-dark">{{ c.code }}</span></td>
              <td>{{ c.name }}</td>
              <td class="text-muted small" style="max-width:320px">{{ c.description || '-' }}</td>
              <td><StatusBadge :status="c.status" /></td>
              <td class="text-end">
                <button class="btn btn-sm btn-light me-1" @click="open(c)" title="Edit Course" aria-label="Edit Course">
                  <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger" @click="del(c)" title="Delete Course" aria-label="Delete Course">
                  <i class="bi bi-trash3"></i>
                </button>
              </td>
            </tr>
            <EmptyRow v-if="!rows.length" :colspan="5" icon="bi-book" text="No courses registered yet. Click &quot;New Course&quot; to get started." />
          </tbody>
        </table>
      </div>
    </div>

    <FormModal v-if="form" :title="form.id ? 'Edit Course' : 'New Course'" :fields="fields" :initial="form.initial" :submit="submit" @close="form = null"/>
  </div>`,
};

const AdminExams = {
  components: { FormModal, Modal },
  setup() {
    const rows = ref([]);
    const courses = ref([]);
    const examiners = ref([]);
    const form = ref(null);
    const rub = ref(null);
    const asg = ref(null);
    const filt = reactive({ q: '', type: '', status: '' });
    const types = ['Viva', 'Practical', 'Project Demo', 'Assessment'];
    const statuses = ['Draft', 'Slot Creation', 'Booking Open', 'Closed', 'Completed'];
    const loading = ref(false);

    const load = async () => {
      loading.value = true;
      try {
        const qs = new URLSearchParams(Object.entries(filt).filter(([, v]) => v)).toString();
        rows.value = await api('GET', '/exams?' + qs);
      } finally {
        loading.value = false;
      }
    };

    onMounted(async () => {
      courses.value = await api('GET', '/courses');
      examiners.value = await api('GET', '/examiners');
      load();
    });
    watch(filt, load);

    const opt = (a) => a.map((x) => ({ value: x, label: x }));
    const fields = computed(() => [
      { key: 'course_id', label: 'Academic Course', type: 'select', required: true, options: courses.value.map((c) => ({ value: c.id, label: c.code + ' &middot; ' + c.name })) },
      { key: 'name', label: 'Examination Title', required: true },
      { key: 'type', label: 'Assessment Type', type: 'select', required: true, options: opt(types) },
      { key: 'duration', label: 'Duration (Minutes)', type: 'number', required: true, min: 1 },
      { key: 'max_marks', label: 'Maximum Marks', type: 'number', required: true, min: 1 },
      { key: 'instructions', label: 'Student Guidelines & Instructions', type: 'textarea' },
      { key: 'slot_start', label: 'Slot Creation Start Date', type: 'date' },
      { key: 'slot_end', label: 'Slot Creation End Date', type: 'date' },
      { key: 'booking_start', label: 'Student Booking Start Date', type: 'date' },
      { key: 'booking_end', label: 'Student Booking End (Cancellation Deadline)', type: 'date' },
    ]);

    const open = (e) => (form.value = { id: e && e.id, initial: e ? { ...e } : { duration: 30, max_marks: 50, type: 'Viva' } });
    const submit = async (d) => {
      await (form.value.id ? api('PUT', '/exams/' + form.value.id, d) : api('POST', '/exams', d));
      toast(form.value.id ? 'Examination updated' : 'Examination created');
      load();
    };
    const setStatus = async (e, status) => {
      await act(() => api('POST', `/exams/${e.id}/status`, { status }), 'Status updated to ' + status);
      load();
    };
    const publish = async (e) => {
      if (!await showConfirm('Publish finalized marks for ' + e.name + '? Results will be instantly visible to all students and can no longer be changed.', { danger: false, confirmText: 'Publish results' })) return;
      let r = false;
      try {
        r = await api('POST', `/exams/${e.id}/publish`);
      } catch (err) {
        if (err.data && err.data.pending_evaluations) {
          // Students are still unevaluated: publishing is final, so ask a second, explicit question.
          if (await showConfirm(err.message + ' Publish anyway?', { confirmText: 'Publish anyway' })) {
            r = await act(() => api('POST', `/exams/${e.id}/publish`, { force: true }));
          }
        } else {
          toast(err.message, 'danger');
        }
      }
      if (r) toast('Results published successfully' + (r.pending_evaluations ? ` (${r.pending_evaluations} students had no evaluation)` : ''));
      load();
    };
    const del = async (e) => {
      if (await showConfirm('Delete ' + e.name + '?', { confirmText: 'Delete' }) && (await act(() => api('DELETE', '/exams/' + e.id), 'Deleted'))) load();
    };

    // Rubrics
    const blankRub = () => ({ criterion: '', max_marks: 10, weightage: 1.0, description: '' });
    const openRub = async (e) => {
      rub.value = {
        exam: e,
        list: await api('GET', `/exams/${e.id}/rubrics`),
        f: blankRub(),
        editing: null,   // id of the criterion being edited, or null when adding a new one
      };
    };
    const editRub = (r) => {
      rub.value.editing = r.id;
      rub.value.f = { criterion: r.criterion, max_marks: r.max_marks, weightage: r.weightage, description: r.description || '' };
    };
    const cancelRubEdit = () => { rub.value.editing = null; rub.value.f = blankRub(); };
    // one form for both: adds a criterion, or saves the one picked with the pencil
    const saveRub = async () => {
      const editing = rub.value.editing;
      const call = editing
        ? () => api('PUT', '/rubrics/' + editing, rub.value.f)
        : () => api('POST', `/exams/${rub.value.exam.id}/rubrics`, rub.value.f);
      if (await act(call, editing ? 'Criterion updated' : 'Criterion added')) {
        rub.value.list = await api('GET', `/exams/${rub.value.exam.id}/rubrics`);
        cancelRubEdit();
      }
    };
    const delRub = async (r) => {
      if (await act(() => api('DELETE', '/rubrics/' + r.id))) {
        rub.value.list = await api('GET', `/exams/${rub.value.exam.id}/rubrics`);
        if (rub.value.editing === r.id) cancelRubEdit();
      }
    };

    // Assign Examiners
    const openAsg = (e) => (asg.value = { exam: e, ids: e.examiners.map((x) => x.id) });
    const saveAsg = async () => {
      if (await act(() => api('PUT', `/exams/${asg.value.exam.id}/examiners`, { examiner_ids: asg.value.ids }), 'Examiners assigned')) {
        asg.value = null;
        load();
      }
    };

    return {
      rows, form, rub, asg, filt, types, statuses, examiners, fields,
      open, submit, setStatus, publish, del, openRub, editRub, saveRub, cancelRubEdit, delRub,
      openAsg, saveAsg, badgeClass, loading,
    };
  },
  template: `
  <div>
    <PageHeader>
      <button class="btn btn-primary" @click="open()">
        <i class="bi bi-plus-lg"></i> New Examination
      </button>
    </PageHeader>

    <FilterBar v-model="filt.q" placeholder="Search examinations by name or course">
      <select class="form-select" v-model="filt.type" aria-label="Filter by type">
        <option value="">All types</option>
        <option v-for="t in types" :key="t">{{ t }}</option>
      </select>
      <select class="form-select" v-model="filt.status" aria-label="Filter by status">
        <option value="">All statuses</option>
        <option v-for="s in statuses" :key="s">{{ s }}</option>
      </select>
    </FilterBar>

    <LoadingBlock v-if="loading" />

    <div v-else class="card">
      <div class="table-responsive">
        <table class="table table-hover mb-0 align-middle">
          <thead>
            <tr>
              <th>Examination</th>
              <th>Course</th>
              <th>Type</th>
              <th>Details</th>
              <th>Examiners</th>
              <th>Status</th>
              <th class="text-end">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in rows" :key="e.id">
              <td>
                <div class="fw-bold text-dark">{{ e.name }}</div>
                <div v-if="e.results_published" class="badge-soft badge-soft-success mt-1">
                  <i class="bi bi-check-circle-fill"></i> Results Published
                </div>
              </td>
              <td><span class="badge-soft badge-soft-dark">{{ e.course_code }}</span></td>
              <td>{{ e.type }}</td>
              <td class="small text-muted">
                <div><i class="bi bi-clock me-1"></i>{{ e.duration }} mins</div>
                <div><i class="bi bi-award me-1"></i>{{ e.max_marks }} marks</div>
              </td>
              <td>
                <div class="small" v-if="e.examiners.length">
                  <span v-for="(ex, i) in e.examiners" :key="ex.id">
                    {{ ex.name }}<span v-if="i < e.examiners.length - 1">, </span>
                  </span>
                </div>
                <span v-else class="text-danger small"><i class="bi bi-exclamation-circle"></i> None</span>
              </td>
              <td>
                <select class="form-select form-select-sm status-select" :value="e.status" :disabled="!e.next_statuses.length"
                        :title="e.results_published ? 'Results are published; the status is locked' : 'Only valid next steps are offered'"
                        aria-label="Examination status" @change="setStatus(e, $event.target.value)">
                  <option v-for="s in [e.status, ...e.next_statuses]" :key="s">{{ s }}</option>
                </select>
              </td>
              <td class="text-end actions-wrap">
                <button class="btn btn-sm btn-light me-1" @click="open(e)" title="Edit Examination" aria-label="Edit Examination">
                  <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm btn-outline-secondary me-1" @click="openRub(e)" title="Manage Rubric Criteria" aria-label="Manage Rubric Criteria">
                  <i class="bi bi-card-checklist"></i> Rubric
                </button>
                <button class="btn btn-sm btn-outline-secondary me-1" @click="openAsg(e)" title="Assign Examiners" aria-label="Assign Examiners">
                  <i class="bi bi-people"></i> Examiners
                </button>
                <button class="btn btn-sm btn-outline-success me-1" @click="publish(e)" :disabled="e.results_published || !['Closed','Completed'].includes(e.status)" :title="['Closed','Completed'].includes(e.status) || e.results_published ? 'Publish Results' : 'Close the examination first'" aria-label="Publish Results">
                  <i class="bi bi-send-check"></i> Publish
                </button>
                <button class="btn btn-sm btn-outline-danger" @click="del(e)" title="Delete Exam" aria-label="Delete Exam">
                  <i class="bi bi-trash3"></i>
                </button>
              </td>
            </tr>
            <EmptyRow v-if="!rows.length" :colspan="7" icon="bi-journal-x" text="No examinations found matching your filter criteria." />
          </tbody>
        </table>
      </div>
    </div>

    <!-- Modals -->
    <FormModal v-if="form" :title="form.id ? 'Edit Examination' : 'New Examination'" :fields="fields" :initial="form.initial" :submit="submit" @close="form = null"/>

    <!-- Rubric Modal -->
    <Modal v-if="rub" :title="'Evaluation Rubrics: ' + rub.exam.name" icon="bi-card-checklist" wide @close="rub = null">
      <div class="panel-inset small text-muted mb-3">
        Define criteria for examiners to score against. Weighted final grade is calculated as:
        <code>(Sum(Marks / Max * Weight) / Sum(Weights)) * Exam Max Marks</code>.
      </div>

      <div class="table-responsive mb-4">
        <table class="table table-sm align-middle">
          <thead>
            <tr>
              <th>Criterion</th>
              <th>Max Marks</th>
              <th>Weight</th>
              <th>Description</th>
              <th class="text-end">Action</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rub.list" :key="r.id">
              <td class="fw-semibold">{{ r.criterion }}</td>
              <td>{{ r.max_marks }}</td>
              <td>{{ r.weightage }}x</td>
              <td class="small text-muted">{{ r.description || '-' }}</td>
              <td class="text-end text-nowrap">
                <button class="btn btn-sm btn-outline-secondary py-0 px-2 me-1" @click="editRub(r)" title="Edit Criterion" aria-label="Edit Criterion"><i class="bi bi-pencil"></i></button>
                <button class="btn btn-sm btn-outline-danger py-0 px-2" @click="delRub(r)" title="Delete Criterion" aria-label="Delete Criterion"><i class="bi bi-x-lg"></i></button>
              </td>
            </tr>
            <EmptyRow v-if="!rub.list.length" :colspan="5" compact text="No rubric criteria added yet. Add at least one below." />
          </tbody>
        </table>
      </div>

      <form class="panel-inset" @submit.prevent="saveRub">
        <h6 class="fw-bold mb-2 small text-uppercase text-muted">{{ rub.editing ? 'Edit Criterion' : 'Add New Criterion' }}</h6>
        <div class="row g-2">
          <div class="col-md-4">
            <input class="form-control form-control-sm" placeholder="Criterion Title (e.g. Code Reading)" aria-label="Criterion title" v-model="rub.f.criterion" required>
          </div>
          <div class="col-4 col-md-2">
            <input type="number" class="form-control form-control-sm" placeholder="Max Marks" aria-label="Maximum marks" min="0.5" step="0.5" v-model="rub.f.max_marks" required>
          </div>
          <div class="col-4 col-md-2">
            <input type="number" class="form-control form-control-sm" placeholder="Weight" aria-label="Weight" min="0.1" step="0.1" v-model="rub.f.weightage" required>
          </div>
          <div class="col-12 col-md-3">
            <input class="form-control form-control-sm" placeholder="Optional notes" aria-label="Notes" v-model="rub.f.description">
          </div>
          <div class="col-md-1">
            <button class="btn btn-sm btn-primary w-100" type="submit" :title="rub.editing ? 'Save changes' : 'Add criterion'" :aria-label="rub.editing ? 'Save changes' : 'Add criterion'">
              <i class="bi" :class="rub.editing ? 'bi-check-lg' : 'bi-plus-lg'"></i>
            </button>
          </div>
        </div>
        <button v-if="rub.editing" type="button" class="btn btn-link btn-sm px-0 mt-2" @click="cancelRubEdit">Cancel editing</button>
      </form>
    </Modal>

    <!-- Examiner Assignment Modal -->
    <Modal v-if="asg" :title="'Assign Examiners &middot; ' + asg.exam.name" icon="bi-people" @close="asg = null">
      <p class="small text-muted mb-3">Select the examiners authorized to open slots and evaluate this examination.</p>
      <div class="list-group mb-3">
        <label v-for="x in examiners.filter(x => x.status === 'Active')" :key="x.id" class="list-group-item d-flex align-items-center gap-3">
          <input class="form-check-input flex-shrink-0" type="checkbox" :value="x.id" v-model="asg.ids">
          <div class="flex-grow-1">
            <div class="fw-semibold">{{ x.name }}</div>
            <div class="small text-muted">{{ x.email }} &middot; {{ x.department || 'General' }}</div>
          </div>
        </label>
      </div>
      <div v-if="!examiners.length" class="text-danger small mb-3">No active examiners found in the system.</div>
      <div class="text-end">
        <button class="btn btn-primary" @click="saveAsg"><i class="bi bi-check2"></i> Save Assignments</button>
      </div>
    </Modal>

  </div>`,
};

const AdminExaminers = {
  components: { FormModal },
  setup() {
    const rows = ref([]);
    const form = ref(null);
    const q = ref('');
    const loading = ref(false);

    const load = async () => {
      loading.value = true;
      try { rows.value = await api('GET', '/examiners?q=' + encodeURIComponent(q.value)); } finally { loading.value = false; }
    };
    onMounted(load);
    watch(q, load);

    const fields = computed(() => [
      { key: 'name', label: 'Full Name', required: true },
      { key: 'email', label: 'Email Address', type: 'email', required: !form.value?.id },
      { key: 'password', label: form.value?.id ? 'New Password (leave blank to keep current)' : 'Password', type: 'password', required: !form.value?.id, minlength: 6 },
      { key: 'phone', label: 'Contact Phone Number', pattern: '[0-9+\\- ]{7,15}' },
      { key: 'department', label: 'Department / Specialization' },
      { key: 'status', label: 'Account Status', type: 'select', required: true, options: [{ value: 'Active', label: 'Active (Approved)' }, { value: 'Inactive', label: 'Inactive (Revoked)' }] },
    ]);

    const open = (x) => (form.value = { id: x && x.id, initial: x ? { ...x, password: '' } : { status: 'Active' } });
    const submit = async (d) => {
      await (form.value.id ? api('PUT', '/examiners/' + form.value.id, d) : api('POST', '/examiners', d));
      toast(form.value.id ? 'Examiner updated' : 'Examiner created');
      load();
    };

    return { rows, form, q, fields, open, submit, badgeClass, loading };
  },
  template: `
  <div>
    <PageHeader>
      <button class="btn btn-primary" @click="open()">
        <i class="bi bi-person-plus"></i> New Examiner
      </button>
    </PageHeader>

    <FilterBar v-model="q" placeholder="Search examiners by name, email or department" />

    <LoadingBlock v-if="loading" />

    <div v-else class="card">
      <div class="table-responsive">
        <table class="table table-hover mb-0 align-middle">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Phone</th>
              <th>Department</th>
              <th>Status</th>
              <th class="text-end">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="x in rows" :key="x.id">
              <td class="fw-bold text-dark">{{ x.name }}</td>
              <td>{{ x.email }}</td>
              <td>{{ x.phone || '-' }}</td>
              <td><span class="badge-soft badge-soft-info">{{ x.department || 'General' }}</span></td>
              <td><StatusBadge :status="x.status" /></td>
              <td class="text-end">
                <button class="btn btn-sm btn-light" @click="open(x)" title="Edit Examiner" aria-label="Edit Examiner">
                  <i class="bi bi-pencil"></i>
                </button>
              </td>
            </tr>
            <EmptyRow v-if="!rows.length" :colspan="6" icon="bi-person-x" text="No examiners found matching the search query." />
          </tbody>
        </table>
      </div>
    </div>

    <FormModal v-if="form" :title="form.id ? 'Edit Examiner' : 'New Examiner'" :fields="fields" :initial="form.initial" :submit="submit" @close="form = null"/>
  </div>`,
};

const AdminStudents = {
  setup() {
    const rows = ref([]);
    const q = ref('');
    const loading = ref(false);

    const load = async () => {
      loading.value = true;
      try { rows.value = await api('GET', '/students?q=' + encodeURIComponent(q.value)); } finally { loading.value = false; }
    };
    onMounted(load);
    watch(q, load);

    const toggle = async (s) => {
      const next = s.status === 'Active' ? 'Inactive' : 'Active';
      await act(() => api('PUT', `/students/${s.id}/status`, { status: next }), 'Student ' + (next === 'Active' ? 'activated' : 'deactivated'));
      load();
    };

    return { rows, q, toggle, badgeClass, loading };
  },
  template: `
  <div>
    <PageHeader />

    <FilterBar v-model="q" placeholder="Search students by name, ID, email or department" />

    <LoadingBlock v-if="loading" />

    <div v-else class="card">
      <div class="table-responsive">
        <table class="table table-hover mb-0 align-middle">
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Email</th>
              <th>Phone</th>
              <th>Department</th>
              <th>Status</th>
              <th class="text-end">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in rows" :key="s.id">
              <td><span class="badge-soft badge-soft-dark">#{{ s.id }}</span></td>
              <td class="fw-bold text-dark">{{ s.name }}</td>
              <td>{{ s.email }}</td>
              <td>{{ s.phone || '-' }}</td>
              <td>{{ s.department || '-' }}</td>
              <td><StatusBadge :status="s.status" /></td>
              <td class="text-end">
                <button class="btn btn-sm" :class="s.status === 'Active' ? 'btn-outline-danger' : 'btn-outline-success'" @click="toggle(s)">
                  <i :class="s.status === 'Active' ? 'bi bi-person-x' : 'bi bi-person-check'"></i>
                  {{ s.status === 'Active' ? 'Deactivate' : 'Activate' }}
                </button>
              </td>
            </tr>
            <EmptyRow v-if="!rows.length" :colspan="7" icon="bi-mortarboard" text="No students match the search criteria." />
          </tbody>
        </table>
      </div>
    </div>
  </div>`,
};

const AdminSlots = {
  components: { FormModal },
  setup() {
    const tab = ref('slots');
    const slots = ref([]);
    const bookings = ref([]);
    const exams = ref([]);
    const examiners = ref([]);
    const q = ref('');
    const examId = ref('');
    const form = ref(null);
    const loading = ref(false);

    const loadSlots = async () => {
      slots.value = await api('GET', '/slots' + (examId.value ? '?exam_id=' + examId.value : ''));
    };
    const loadBookings = async () => {
      bookings.value = await api('GET', '/bookings?q=' + encodeURIComponent(q.value));
    };

    const loadAll = async () => {
      loading.value = true;
      try { await Promise.all([loadSlots(), loadBookings()]); } finally { loading.value = false; }
    };

    onMounted(async () => {
      exams.value = await api('GET', '/exams');
      examiners.value = await api('GET', '/examiners');
      loadAll();
    });

    watch(examId, loadSlots);
    watch(q, loadBookings);

    const examExaminers = (s) => exams.value.find((e) => e.id === s.exam_id)?.examiners || [];
    const reassign = async (s, id) => {
      if (id && (await act(() => api('PUT', `/slots/${s.id}/examiner`, { examiner_id: +id }), 'Examiner reassigned'))) loadAll();
    };
    const cancel = async (s) => {
      if (await showConfirm('Cancel this slot? Any booked students will have their appointments invalidated.', { confirmText: 'Cancel slot', cancelText: 'Keep slot' })) {
        if (await act(() => api('DELETE', '/slots/' + s.id), 'Slot cancelled')) loadAll();
      }
    };

    const fields = [
      { key: 'date', label: 'Date', type: 'date', required: true },
      { key: 'start_time', label: 'Start Time', type: 'time', required: true },
      { key: 'end_time', label: 'End Time', type: 'time', required: true },
      { key: 'capacity', label: 'Capacity', type: 'number', min: 1, required: true },
      { key: 'meeting_link', label: 'Meeting Link (optional)', type: 'url' },
    ];

    const newFields = computed(() => [
      { key: 'exam_id', label: 'Examination', type: 'select', required: true, options: exams.value.map((e) => ({ value: e.id, label: e.name })) },
      { key: 'examiner_id', label: 'Examiner', type: 'select', required: true, options: examiners.value.filter((x) => x.status === 'Active').map((x) => ({ value: x.id, label: x.name })) },
      ...fields,
    ]);

    const open = (s) => (form.value = s ? { id: s.id, initial: { ...s } } : { initial: { capacity: 1 } });
    const submit = async (d) => {
      await (form.value.id ? api('PUT', '/slots/' + form.value.id, d) : api('POST', '/slots', d));
      toast(form.value.id ? 'Slot rescheduled successfully' : 'Slot created successfully');
      loadAll();
    };

    const targets = (b) => slots.value.filter((s) => s.exam_id === b.exam_id && s.id !== b.slot_id && s.status === 'Available');
    const move = async (b, id) => {
      if (id && (await act(() => api('PUT', `/bookings/${b.id}/reschedule`, { slot_id: +id }), 'Booking rescheduled'))) loadAll();
    };

    return {
      tab, slots, bookings, exams, q, examId, form, examExaminers,
      reassign, cancel, fields, newFields, open, submit, targets, move,
      badgeClass, fmtDate, loading,
    };
  },
  template: `
  <div>
    <PageHeader>
      <button class="btn btn-primary" @click="open()">
        <i class="bi bi-calendar-plus"></i> New Slot
      </button>
    </PageHeader>

    <ul class="nav nav-pills mb-3 bg-white p-1 rounded-3 border d-inline-flex">
      <li class="nav-item">
        <a class="nav-link" :class="{ active: tab === 'slots' }" href="#" @click.prevent="tab = 'slots'">
          <i class="bi bi-calendar3 me-1"></i> Examination Slots ({{ slots.length }})
        </a>
      </li>
      <li class="nav-item">
        <a class="nav-link" :class="{ active: tab === 'bookings' }" href="#" @click.prevent="tab = 'bookings'">
          <i class="bi bi-ticket-detailed me-1"></i> Student Bookings ({{ bookings.length }})
        </a>
      </li>
    </ul>

    <LoadingBlock v-if="loading" />

    <div v-else>
      <!-- Slots Tab -->
      <div v-if="tab === 'slots'">
        <FilterBar>
          <select class="form-select" v-model="examId" aria-label="Filter by examination">
            <option value="">All examinations</option>
            <option v-for="e in exams" :key="e.id" :value="e.id">{{ e.name }}</option>
          </select>
        </FilterBar>

        <div class="card">
          <div class="table-responsive">
            <table class="table table-hover mb-0 align-middle">
              <thead>
                <tr>
                  <th>Examination</th>
                  <th>Date</th>
                  <th>Time</th>
                  <th>Examiners</th>
                  <th>Seats</th>
                  <th>Status</th>
                  <th class="text-end">Actions</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in slots" :key="s.id">
                  <td class="fw-bold text-dark">{{ s.exam_name }}</td>
                  <td>{{ fmtDate(s.date) }}</td>
                  <td><span class="badge-soft badge-soft-dark">{{ s.start_time }} &ndash; {{ s.end_time }}</span></td>
                  <td>
                    <select class="form-select form-select-sm" :value="s.examiner_id" @change="reassign(s, $event.target.value)">
                      <option v-for="x in examExaminers(s)" :key="x.id" :value="x.id">{{ x.name }}</option>
                    </select>
                  </td>
                  <td>
                    <SeatMeter :booked="s.booked" :capacity="s.capacity" />
                  </td>
                  <td><StatusBadge :status="s.status" /></td>
                  <td class="text-end text-nowrap">
                    <button class="btn btn-sm btn-light me-1" @click="open(s)" title="Reschedule Slot" aria-label="Reschedule Slot">
                      <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" :disabled="s.status === 'Cancelled'" @click="cancel(s)" title="Cancel Slot" aria-label="Cancel Slot">
                      <i class="bi bi-x-circle"></i>
                    </button>
                  </td>
                </tr>
                <EmptyRow v-if="!slots.length" :colspan="7" icon="bi-calendar-x" text="No examination slots found." />
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Bookings Tab -->
      <div v-else>
        <FilterBar v-model="q" placeholder="Search bookings by student, email, examination or booking ID" />

        <div class="card">
          <div class="table-responsive">
            <table class="table table-hover mb-0 align-middle">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Student</th>
                  <th>Exam Title</th>
                  <th>Slot Schedule</th>
                  <th>Examiner</th>
                  <th>Status</th>
                  <th>Marks</th>
                  <th>Move to Slot</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="b in bookings" :key="b.id">
                  <td><span class="badge-soft badge-soft-dark">#{{ b.id }}</span></td>
                  <td class="fw-bold text-dark">{{ b.student_name }}</td>
                  <td>{{ b.exam_name }}</td>
                  <td>{{ fmtDate(b.date) }} {{ b.start_time }}</td>
                  <td>{{ b.examiner_name }}</td>
                  <td><StatusBadge :status="b.status" /></td>
                  <td>
                    <span v-if="b.evaluation" class="fw-bold text-success">{{ b.evaluation.total_marks }} / {{ b.max_marks }}</span>
                    <span v-else class="text-muted small">&ndash;</span>
                  </td>
                  <td>
                    <select v-if="b.status === 'Booked'" class="form-select form-select-sm" @change="move(b, $event.target.value); $event.target.value = ''">
                      <option value="">Move to another slot...</option>
                      <option v-for="s in targets(b)" :key="s.id" :value="s.id">
                        {{ fmtDate(s.date) }} {{ s.start_time }} &middot; {{ s.examiner_name }} ({{ s.available_seats }} seats free)
                      </option>
                    </select>
                  </td>
                </tr>
                <EmptyRow v-if="!bookings.length" :colspan="8" icon="bi-ticket" text="No bookings match the search criteria." />
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <FormModal v-if="form" :title="form.id ? 'Reschedule Slot' : 'New Slot'" :fields="form.id ? fields : newFields" :initial="form.initial" :submit="submit" @close="form = null"/>
  </div>`,
};

const AdminSearch = {
  setup() {
    const q = ref('');
    const r = ref(null);
    const loading = ref(false);
    let t;

    watch(q, () => {
      clearTimeout(t);
      if (!q.value.trim()) { r.value = null; return; }
      loading.value = true;
      t = setTimeout(async () => {
        try {
          r.value = await api('GET', '/admin/search?q=' + encodeURIComponent(q.value));
        } finally {
          loading.value = false;
        }
      }, 300);
    });

    return { q, r, loading, badgeClass };
  },
  template: `
  <div>
    <PageHeader />

    <FilterBar v-model="q" placeholder="Search students, examiners, examinations and bookings" autofocus />

    <LoadingBlock v-if="loading" />

    <div v-else-if="r" class="row g-3">
      <!-- Students -->
      <div class="col-md-6">
        <div class="card h-100">
          <div class="card-title-bar">
            <span><i class="bi bi-mortarboard"></i>Students</span>
            <span class="badge-soft badge-soft-primary">{{ r.students.length }}</span>
          </div>
          <ul class="list-group list-group-flush">
            <li class="list-group-item" v-for="s in r.students" :key="s.id">
              <div class="fw-semibold">#{{ s.id }} {{ s.name }}</div>
              <div class="small text-muted">{{ s.email }} &middot; {{ s.department || 'General' }}</div>
            </li>
            <li v-if="!r.students.length" class="list-group-item text-muted small py-3 text-center">No students found</li>
          </ul>
        </div>
      </div>

      <!-- Examiners -->
      <div class="col-md-6">
        <div class="card h-100">
          <div class="card-title-bar">
            <span><i class="bi bi-person-badge"></i>Examiners</span>
            <span class="badge-soft badge-soft-info">{{ r.examiners.length }}</span>
          </div>
          <ul class="list-group list-group-flush">
            <li class="list-group-item" v-for="x in r.examiners" :key="x.id">
              <div class="fw-semibold">{{ x.name }}</div>
              <div class="small text-muted">{{ x.email }} &middot; {{ x.department || 'General' }}</div>
            </li>
            <li v-if="!r.examiners.length" class="list-group-item text-muted small py-3 text-center">No examiners found</li>
          </ul>
        </div>
      </div>

      <!-- Examinations -->
      <div class="col-md-6">
        <div class="card h-100">
          <div class="card-title-bar">
            <span><i class="bi bi-journal-check"></i>Examinations</span>
            <span class="badge-soft badge-soft-dark">{{ r.exams.length }}</span>
          </div>
          <ul class="list-group list-group-flush">
            <li class="list-group-item" v-for="e in r.exams" :key="e.id">
              <div class="d-flex justify-content-between align-items-center">
                <div class="fw-semibold">{{ e.name }}</div>
                <StatusBadge :status="e.status" />
              </div>
              <div class="small text-muted">{{ e.course_code }} &middot; {{ e.type }}</div>
            </li>
            <li v-if="!r.exams.length" class="list-group-item text-muted small py-3 text-center">No examinations found</li>
          </ul>
        </div>
      </div>

      <!-- Bookings -->
      <div class="col-md-6">
        <div class="card h-100">
          <div class="card-title-bar">
            <span><i class="bi bi-ticket-detailed"></i>Bookings</span>
            <span class="badge-soft badge-soft-warning">{{ r.bookings.length }}</span>
          </div>
          <ul class="list-group list-group-flush">
            <li class="list-group-item" v-for="b in r.bookings" :key="b.id">
              <div class="d-flex justify-content-between align-items-center">
                <span class="fw-semibold">#{{ b.id }} {{ b.student_name }}</span>
                <StatusBadge :status="b.status" />
              </div>
              <div class="small text-muted">&rarr; {{ b.exam_name }}</div>
            </li>
            <li v-if="!r.bookings.length" class="list-group-item text-muted small py-3 text-center">No bookings found</li>
          </ul>
        </div>
      </div>
    </div>
  </div>`,
};

/* ---------------- Examiner Pages ---------------- */
const ExaminerDash = {
  setup() {
    const d = ref(null);
    const loading = ref(false);

    const load = async () => {
      loading.value = true;
      try { d.value = await api('GET', '/dashboard'); } finally { loading.value = false; }
    };
    onMounted(load);

    const setStatus = async (e, status) => {
      await act(() => api('POST', `/exams/${e.id}/status`, { status }), 'Examination marked ' + status.toLowerCase());
      load();
    };

    const firstName = computed(() => firstNameOf(state.user && state.user.name));
    return { d, setStatus, badgeClass, fmtDate, loading, firstName };
  },
  template: `
  <div>
    <HeroBanner :title="'Hello, ' + firstName + ' 👋'" subtitle="Manage your slots, evaluate students against rubrics and close out examinations." />

    <LoadingBlock v-if="loading" />

    <div v-else-if="d">
      <!-- Stat Tiles -->
      <div class="row g-3 mb-4">
        <div class="col-md-4">
          <StatCard tone="info" icon="bi-calendar-check" label="My Created Slots" :value="d.slots" />
        </div>
        <div class="col-md-4">
          <StatCard tone="primary" icon="bi-people" label="Booked Students" :value="d.booked_students" />
        </div>
        <div class="col-md-4">
          <StatCard tone="warning" icon="bi-hourglass-split" label="Pending Evaluations" :value="d.pending_evaluations" />
        </div>
      </div>

      <!-- Assigned Exams -->
      <div class="card mb-4">
        <div class="card-title-bar">
          <span><i class="bi bi-journal-text"></i>Assigned Examinations</span>
          <router-link to="/examiner/slots" class="btn btn-sm btn-outline-primary">
            <i class="bi bi-calendar-plus"></i> Open Slots
          </router-link>
        </div>
        <div class="table-responsive">
          <table class="table table-hover mb-0 align-middle">
            <thead>
              <tr>
                <th>Title</th>
                <th>Course</th>
                <th>Type</th>
                <th>Status</th>
                <th class="text-end">Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="e in d.assigned_exams" :key="e.id">
                <td class="fw-bold text-dark">{{ e.name }}</td>
                <td><span class="badge-soft badge-soft-dark">{{ e.course_code }}</span></td>
                <td>{{ e.type }}</td>
                <td><StatusBadge :status="e.status" /></td>
                <td class="text-end text-nowrap">
                  <button class="btn btn-sm btn-outline-secondary me-1" :disabled="!e.next_statuses.includes('Closed')" @click="setStatus(e, 'Closed')">
                    <i class="bi bi-door-closed"></i> Close
                  </button>
                  <button class="btn btn-sm btn-outline-success" :disabled="!e.next_statuses.includes('Completed')" :title="e.status === 'Closed' ? '' : 'Close the examination first'" @click="setStatus(e, 'Completed')">
                    <i class="bi bi-check2-all"></i> Mark Completed
                  </button>
                </td>
              </tr>
              <EmptyRow v-if="!d.assigned_exams.length" :colspan="5" icon="bi-inbox" text="No examinations currently assigned to your profile." />
            </tbody>
          </table>
        </div>
      </div>

      <!-- Created Slots -->
      <div class="card">
        <div class="card-title-bar">
          <span><i class="bi bi-clock-history"></i>Recent Availability Slots</span>
          <router-link to="/examiner/evaluate" class="btn btn-sm btn-primary">
            <i class="bi bi-check2-square"></i> Go to Evaluations
          </router-link>
        </div>
        <div class="table-responsive">
          <table class="table table-hover mb-0 align-middle">
            <thead>
              <tr>
                <th>Examination</th>
                <th>Date</th>
                <th>Time</th>
                <th>Capacity</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in d.slot_list" :key="s.id">
                <td class="fw-bold text-dark">{{ s.exam_name }}</td>
                <td>{{ fmtDate(s.date) }}</td>
                <td><span class="badge-soft badge-soft-dark">{{ s.start_time }} &ndash; {{ s.end_time }}</span></td>
                <td><SeatMeter :booked="s.booked" :capacity="s.capacity" /></td>
                <td><StatusBadge :status="s.status" /></td>
              </tr>
              <EmptyRow v-if="!d.slot_list.length" :colspan="5" icon="bi-calendar-x" text="No examination slots created yet." />
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>`,
};

const ExaminerSlots = {
  components: { FormModal },
  setup() {
    const slots = ref([]);
    const exams = ref([]);
    const form = ref(null);
    const loading = ref(false);
    const load = async () => {
      loading.value = true;
      try { slots.value = await api('GET', '/slots'); } finally { loading.value = false; }
    };

    onMounted(async () => {
      exams.value = await api('GET', '/exams');
      load();
    });

    const fields = computed(() => [
      { key: 'exam_id', label: 'Examination', type: 'select', required: true, options: exams.value.map((e) => ({ value: e.id, label: `${e.name} (${e.status})` })) },
      { key: 'date', label: 'Date', type: 'date', required: true },
      { key: 'start_time', label: 'Start Time', type: 'time', required: true },
      { key: 'end_time', label: 'End Time', type: 'time', required: true },
      { key: 'capacity', label: 'Capacity', type: 'number', min: 1, required: true },
      { key: 'meeting_link', label: 'Meeting Link (optional)', type: 'url' },
    ]);

    const open = (s) => (form.value = { id: s && s.id, initial: s ? { ...s } : { capacity: 1 } });
    const submit = async (d) => {
      await (form.value.id ? api('PUT', '/slots/' + form.value.id, d) : api('POST', '/slots', d));
      toast('Availability slot saved');
      load();
    };
    const del = async (s) => {
      if (await showConfirm('Remove this slot from the examination schedule?', { confirmText: 'Remove slot', cancelText: 'Keep slot' })) {
        if (await act(() => api('DELETE', '/slots/' + s.id), 'Slot removed')) load();
      }
    };

    return { slots, form, fields, open, submit, del, badgeClass, fmtDate, loading };
  },
  template: `
  <div>
    <PageHeader>
      <button class="btn btn-primary" @click="open()">
        <i class="bi bi-calendar-plus"></i> New Slot
      </button>
    </PageHeader>

    <LoadingBlock v-if="loading" />

    <div v-else class="card">
      <div class="table-responsive">
        <table class="table table-hover mb-0 align-middle">
          <thead>
            <tr>
              <th>Examination</th>
              <th>Date</th>
              <th>Time</th>
              <th>Capacity</th>
              <th>Meeting Link</th>
              <th>Status</th>
              <th class="text-end">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in slots" :key="s.id">
              <td class="fw-bold text-dark">{{ s.exam_name }}</td>
              <td>{{ fmtDate(s.date) }}</td>
              <td><span class="badge-soft badge-soft-dark">{{ s.start_time }} &ndash; {{ s.end_time }}</span></td>
              <td><SeatMeter :booked="s.booked" :capacity="s.capacity" /></td>
              <td>
                <a v-if="s.meeting_link" :href="s.meeting_link" target="_blank" rel="noopener noreferrer" class="small d-inline-flex align-items-center gap-1">
                  <i class="bi bi-camera-video"></i> Join Meeting
                </a>
                <span v-else class="text-muted small">&ndash;</span>
              </td>
              <td><StatusBadge :status="s.status" /></td>
              <td class="text-end text-nowrap">
                <button class="btn btn-sm btn-light me-1" :disabled="s.status === 'Cancelled'" @click="open(s)" title="Edit Slot" aria-label="Edit Slot">
                  <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger" :disabled="s.status === 'Cancelled'" @click="del(s)" title="Cancel Slot" aria-label="Cancel Slot">
                  <i class="bi bi-x-circle"></i>
                </button>
              </td>
            </tr>
            <EmptyRow v-if="!slots.length" :colspan="7" icon="bi-calendar-plus" text="No slots created yet. Slots can be opened while examinations are in &quot;Slot Creation&quot;." />
          </tbody>
        </table>
      </div>
    </div>

    <FormModal v-if="form" :title="form.id ? 'Edit Slot' : 'New Slot'" :fields="fields" :initial="form.initial" :submit="submit" @close="form = null"/>
  </div>`,
};

const ExaminerEvaluate = {
  components: { Modal },
  setup() {
    const slots = ref([]);
    const view = ref(null);
    const ev = ref(null);
    const loading = ref(false);

    const load = async () => {
      loading.value = true;
      try { slots.value = await api('GET', '/slots'); } finally { loading.value = false; }
    };
    onMounted(load);

    const open = async (s) => {
      view.value = { slot: s, list: await api('GET', `/slots/${s.id}/bookings`) };
    };

    const startEval = async (b) => {
      const rubrics = await api('GET', `/exams/${b.exam_id}/rubrics`);
      const prev = {};
      (b.evaluation?.scores || []).forEach((s) => (prev[s.rubric_id] = s.marks));
      ev.value = {
        b,
        rubrics,
        marks: Object.fromEntries(rubrics.map((r) => [r.id, prev[r.id] ?? ''])),
        remarks: b.evaluation?.remarks || '',
      };
    };

    // Real-time dynamic weighted score calculation for instantaneous UX feedback!
    const liveScore = computed(() => {
      if (!ev.value || !ev.value.rubrics.length) return null;
      let weighted = 0;
      let wsum = 0;
      for (const r of ev.value.rubrics) {
        const m = parseFloat(ev.value.marks[r.id]);
        if (!isNaN(m)) {
          weighted += (m / r.max_marks) * r.weightage;
          wsum += r.weightage;
        }
      }
      if (wsum === 0) return 0;
      const total = Math.round(((weighted / wsum) * ev.value.b.max_marks) * 100) / 100;
      return total;
    });

    const save = async () => {
      const body = {
        scores: Object.entries(ev.value.marks).map(([rubric_id, marks]) => ({ rubric_id: +rubric_id, marks: parseFloat(marks) || 0 })),
        remarks: ev.value.remarks,
      };
      if (await act(() => api('POST', `/bookings/${ev.value.b.id}/evaluate`, body), 'Evaluation saved')) {
        ev.value = null;
        await open(view.value.slot);
        load();
      }
    };

    const complete = async (s) => {
      if (await act(() => api('POST', `/slots/${s.id}/complete`), 'Slot marked as completed')) {
        view.value = null;
        load();
      }
    };

    return { slots, view, ev, liveScore, open, startEval, save, complete, badgeClass, fmtDate, loading };
  },
  template: `
  <div>
    <PageHeader />

    <LoadingBlock v-if="loading" />

    <div v-else class="card">
      <div class="table-responsive">
        <table class="table table-hover mb-0 align-middle">
          <thead>
            <tr>
              <th>Examination</th>
              <th>Date</th>
              <th>Time</th>
              <th>Booked Students</th>
              <th>Status</th>
              <th class="text-end">Action</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in slots.filter(x => x.status !== 'Cancelled')" :key="s.id">
              <td class="fw-bold text-dark">{{ s.exam_name }}</td>
              <td>{{ fmtDate(s.date) }}</td>
              <td><span class="badge-soft badge-soft-dark">{{ s.start_time }} &ndash; {{ s.end_time }}</span></td>
              <td><span class="badge-soft badge-soft-primary">{{ s.booked }} student(s)</span></td>
              <td><StatusBadge :status="s.status" /></td>
              <td class="text-end">
                <button class="btn btn-sm btn-primary" @click="open(s)">
                  <i class="bi bi-people-fill"></i> View Students
                </button>
              </td>
            </tr>
            <EmptyRow v-if="!slots.length" :colspan="6" icon="bi-check2-circle" text="No evaluation slots available." />
          </tbody>
        </table>
      </div>
    </div>

    <!-- Slot roster modal -->
    <Modal v-if="view" :title="view.slot.exam_name + ' &middot; ' + fmtDate(view.slot.date)" icon="bi-people" wide @close="view = null">
      <div class="table-responsive mb-3">
        <table class="table table-hover align-middle">
          <thead>
            <tr>
              <th>Student</th>
              <th>Status</th>
              <th>Marks</th>
              <th class="text-end">Action</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in view.list" :key="b.id">
              <td class="fw-bold text-dark">{{ b.student_name }}</td>
              <td><StatusBadge :status="b.status" /></td>
              <td>
                <span v-if="b.evaluation" class="fw-bold text-success">{{ b.evaluation.total_marks }} / {{ b.max_marks }}</span>
                <span v-else class="badge-soft badge-soft-warning"><i class="bi bi-hourglass me-1"></i>Pending</span>
              </td>
              <td class="text-end">
                <button class="btn btn-sm btn-primary" :disabled="b.results_published" @click="startEval(b)">
                  <i class="bi bi-pencil-square"></i> {{ b.evaluated ? 'Re-evaluate' : 'Evaluate' }}
                </button>
              </td>
            </tr>
            <EmptyRow v-if="!view.list.length" :colspan="4" compact text="No students booked for this slot." />
          </tbody>
        </table>
      </div>

      <div class="d-flex justify-content-between align-items-center pt-2 border-top">
        <span class="small text-muted">Complete the slot once all students in this session have been scored.</span>
        <button class="btn btn-success" @click="complete(view.slot)">
          <i class="bi bi-check-all"></i> Mark Slot Completed
        </button>
      </div>
    </Modal>

    <!-- Rubric Scoring Modal with Real-time Calculation -->
    <Modal v-if="ev" :title="'Evaluate: ' + ev.b.student_name" icon="bi-award" @close="ev = null">
      <div class="panel-inset mb-3 d-flex justify-content-between align-items-center">
        <div>
          <div class="small text-muted">Total Calculated Score</div>
          <div class="fs-4 fw-bold text-primary">{{ liveScore ?? 0 }} / {{ ev.b.max_marks }}</div>
        </div>
        <span class="badge-soft badge-soft-primary">Live Weighted Calculation</span>
      </div>

      <form @submit.prevent="save">
        <div class="mb-3 panel-inset" v-for="r in ev.rubrics" :key="r.id">
          <div class="d-flex justify-content-between align-items-center mb-1">
            <label class="form-label fw-bold mb-0">{{ r.criterion }}</label>
            <span class="badge-soft badge-soft-dark">Max: {{ r.max_marks }} (Weight: {{ r.weightage }}x)</span>
          </div>
          <div class="text-muted small mb-2" v-if="r.description">{{ r.description }}</div>
          <div class="input-group">
            <span class="input-group-text bg-light text-muted">Marks</span>
            <input type="number" class="form-control" v-model="ev.marks[r.id]" min="0" :max="r.max_marks" step="0.5" placeholder="Enter score" required>
            <span class="input-group-text bg-light text-muted">/ {{ r.max_marks }}</span>
          </div>
        </div>

        <div class="mb-3">
          <label class="form-label small fw-semibold text-muted">Examiner Remarks &amp; Feedback</label>
          <textarea class="form-control" rows="3" v-model="ev.remarks" placeholder="Provide constructive feedback for the student's performance..."></textarea>
        </div>

        <p v-if="!ev.rubrics.length" class="text-danger small">No rubric defined for this examination.</p>

        <div class="d-flex justify-content-end gap-2">
          <button type="button" class="btn btn-light" @click="ev = null">Cancel</button>
          <button class="btn btn-primary" :disabled="!ev.rubrics.length">
            <i class="bi bi-check2"></i> Submit Final Score
          </button>
        </div>
      </form>
    </Modal>
  </div>`,
};

/* ---------------- Student Pages ---------------- */
const StudentDash = {
  setup() {
    const d = ref(null);
    const job = ref(null);
    const loading = ref(false);

    const load = async () => {
      loading.value = true;
      try { d.value = await api('GET', '/dashboard'); } finally { loading.value = false; }
    };
    onMounted(load);

    let pollTimer = null;
    onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer); });

    const exportCsv = async () => {
      const j = await act(() => api('POST', '/export/history'), 'Export background task queued. Preparing CSV...');
      if (!j) return;
      job.value = j;
      let tries = 0;
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(async () => {
        if (++tries > 60) { clearInterval(pollTimer); pollTimer = null; toast('Export is taking long. Check back later.', 'danger'); return; }
        try {
          const s = await api('GET', '/export/' + j.id);
          if (s.status !== 'Pending') {
            clearInterval(pollTimer);
            pollTimer = null;
            job.value = s;
            toast(s.status === 'Done' ? 'Your CSV export is ready to download' : 'Export failed', s.status === 'Done' ? 'success' : 'danger');
          }
        } catch (e) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      }, 1500);
    };

    const downloadCsv = async () => {
      const r = await fetch(`/api/export/${job.value.id}/download`, { headers: { Authorization: 'Bearer ' + token() } });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await r.blob());
      a.download = 'examination_history.csv';
      a.click();
    };

    return {
      d, job, exportCsv, downloadCsv, cardColor, fmtDay, fmtDate,
      user: computed(() => state.user || {}), loading,
      firstName: computed(() => firstNameOf(state.user && state.user.name)),
    };
  },
  template: `
  <div>
    <LoadingBlock v-if="loading" />

    <div v-else-if="d">
      <HeroBanner :title="'Hello, ' + firstName + ' 👋'"
                :subtitle="d.upcoming.length ? 'You have ' + d.upcoming.length + ' upcoming examination' + (d.upcoming.length > 1 ? 's' : '') + '. Check your slot details below.' : 'No upcoming examinations. Browse open examinations to book a slot.'">
        <button class="btn btn-light" :disabled="job && job.status === 'Pending'" @click="exportCsv">
          <i class="bi bi-file-earmark-spreadsheet text-success"></i>
          {{ job && job.status === 'Pending' ? 'Generating CSV...' : 'Export History (CSV)' }}
        </button>
        <button v-if="job && job.status === 'Done'" class="btn btn-success" @click="downloadCsv">
          <i class="bi bi-download"></i> Download File
        </button>
      </HeroBanner>

      <!-- Stat Tiles -->
      <div class="row g-3 mb-4">
        <div class="col-6 col-md-3">
          <StatCard tone="primary" icon="bi-journal-text" label="Open Exams" :value="d.available_exams.length" />
        </div>
        <div class="col-6 col-md-3">
          <StatCard tone="info" icon="bi-calendar3" label="Available Slots" :value="d.available_slots" />
        </div>
        <div class="col-6 col-md-3">
          <StatCard tone="warning" icon="bi-clock-history" label="Booked Sessions" :value="d.booked" />
        </div>
        <div class="col-6 col-md-3">
          <StatCard tone="success" icon="bi-trophy" label="Published Results" :value="d.results.length" />
        </div>
      </div>

      <!-- Main Columns: Available Exams vs Upcoming Agenda -->
      <div class="row g-4">
        <div class="col-lg-8">
          <div class="d-flex justify-content-between align-items-center mb-3">
            <h2 class="section-title">Open for Booking</h2>
            <router-link to="/student/exams" class="small fw-semibold">View all &rarr;</router-link>
          </div>

          <div class="row g-3 mb-4">
            <div class="col-md-6" v-for="e in d.available_exams" :key="e.id">
              <div class="card exam-card h-100">
                <div class="head" :style="{ background: cardColor(e.id) }">
                  <span class="chip">{{ e.type }}</span>
                  <div class="t">{{ e.name }}</div>
                  <div class="s">{{ e.course_code }} &middot; {{ e.course_name }}</div>
                </div>
                <div class="body d-flex justify-content-between align-items-center">
                  <div class="small text-muted">
                    <i class="bi bi-calendar-event me-1"></i>Book by {{ fmtDate(e.booking_end) }}
                  </div>
                  <router-link class="btn btn-sm btn-primary" to="/student/exams">
                    Book Slot
                  </router-link>
                </div>
              </div>
            </div>
            <div v-if="!d.available_exams.length" class="col-12">
              <div class="card empty-state compact">
                <div class="empty-icon"><i class="bi bi-journal-x"></i></div>
                <div>No examinations are open for booking right now.</div>
              </div>
            </div>
          </div>

          <!-- Published Results -->
          <div class="d-flex justify-content-between align-items-center mb-3">
            <h2 class="section-title">Recent Results</h2>
            <router-link to="/student/results" class="small fw-semibold">Scorecards &rarr;</router-link>
          </div>
          <div class="card">
            <ul class="list-group list-group-flush">
              <li class="list-group-item d-flex justify-content-between align-items-center py-3" v-for="b in d.results" :key="b.id">
                <div>
                  <div class="fw-semibold text-dark">{{ b.exam_name }}</div>
                  <div class="small text-muted">{{ b.course }} &middot; Examiner: {{ b.examiner_name }}</div>
                </div>
                <div class="text-end">
                  <div class="fs-5 fw-bold text-success">{{ b.evaluation.total_marks }} / {{ b.max_marks }}</div>
                  <router-link to="/student/results" class="small">View Scorecard</router-link>
                </div>
              </li>
              <li v-if="!d.results.length" class="list-group-item empty-state compact">No results published yet.</li>
            </ul>
          </div>
        </div>

        <!-- Right Column: Agenda / To Do -->
        <div class="col-lg-4">
          <div class="card">
            <div class="card-body">
              <h2 class="section-title mb-3 d-flex align-items-center gap-2">
                <i class="bi bi-calendar-check text-primary"></i> Upcoming Schedule
              </h2>
              <div class="todo-item" v-for="b in d.upcoming" :key="b.id">
                <div class="todo-date">
                  <b>{{ fmtDay(b.date).d }}</b>
                  <small>{{ fmtDay(b.date).m }}</small>
                </div>
                <div class="flex-grow-1">
                  <div class="fw-semibold text-dark">{{ b.exam_name }}</div>
                  <div class="small text-muted">{{ b.start_time }} &ndash; {{ b.end_time }} &middot; {{ b.examiner_name }}</div>
                  <a v-if="b.meeting_link" :href="b.meeting_link" target="_blank" rel="noopener noreferrer" class="small text-primary fw-semibold d-inline-flex align-items-center gap-1 mt-1">
                    <i class="bi bi-camera-video"></i> Join Meeting
                  </a>
                </div>
              </div>
              <div v-if="!d.upcoming.length" class="empty-state compact">
                <div class="empty-icon"><i class="bi bi-cup-hot"></i></div>
                <div>No upcoming examinations.</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>`,
};

const StudentExams = {
  components: { Modal },
  setup() {
    const rows = ref([]);
    const view = ref(null);
    const filt = reactive({ q: '', type: '', status: '' });
    const types = ['Viva', 'Practical', 'Project Demo', 'Assessment'];
    const loading = ref(false);

    const load = async () => {
      loading.value = true;
      try {
        const qs = new URLSearchParams(Object.entries(filt).filter(([, v]) => v)).toString();
        rows.value = await api('GET', '/exams?' + qs);
      } finally {
        loading.value = false;
      }
    };
    onMounted(load);
    watch(filt, load);

    const openSlots = async (e) => {
      view.value = {
        exam: e,
        slots: await api('GET', '/slots?exam_id=' + e.id),
        rubrics: await api('GET', `/exams/${e.id}/rubrics`),
      };
    };

    const book = async (s) => {
      if (await act(() => api('POST', '/bookings', { slot_id: s.id }), 'Slot booked successfully')) {
        view.value.slots = await api('GET', '/slots?exam_id=' + s.exam_id);
      }
    };

    return { rows, view, filt, types, openSlots, book, badgeClass, cardColor, fmtDate, loading };
  },
  template: `
  <div>
    <PageHeader />

    <FilterBar v-model="filt.q" placeholder="Search examinations by name or course">
      <select class="form-select" v-model="filt.type" aria-label="Filter by type">
        <option value="">All types</option>
        <option v-for="t in types" :key="t">{{ t }}</option>
      </select>
      <select class="form-select" v-model="filt.status" aria-label="Filter by status">
        <option value="">All statuses</option>
        <option>Slot Creation</option>
        <option>Booking Open</option>
        <option>Closed</option>
        <option>Completed</option>
      </select>
    </FilterBar>

    <LoadingBlock v-if="loading" />

    <!-- Exam Cards Grid -->
    <div v-else class="row g-3">
      <div class="col-md-6 col-lg-4" v-for="e in rows" :key="e.id">
        <div class="card exam-card h-100">
          <div class="head" :style="{ background: cardColor(e.id) }">
            <span class="chip">{{ e.status }}</span>
            <div class="t">{{ e.name }}</div>
            <div class="s">{{ e.course_code }} &middot; {{ e.course_name }}</div>
          </div>
          <div class="body d-flex flex-column justify-content-between">
            <div class="mb-3">
              <div class="d-flex align-items-center gap-3 small text-muted mb-2">
                <span><i class="bi bi-tag me-1"></i>{{ e.type }}</span>
                <span><i class="bi bi-clock me-1"></i>{{ e.duration }}m</span>
                <span><i class="bi bi-award me-1"></i>{{ e.max_marks }} marks</span>
              </div>
              <div class="small text-secondary mb-2">
                <i class="bi bi-calendar-range me-1"></i>Booking Window: <b>{{ fmtDate(e.booking_start) }}</b> &ndash; <b>{{ fmtDate(e.booking_end) }}</b>
              </div>
              <p class="small text-muted mb-0" style="min-height: 38px;">{{ e.instructions || 'No specific instructions posted.' }}</p>
            </div>
            <button class="btn btn-sm w-100" :class="e.status === 'Booking Open' ? 'btn-primary' : 'btn-outline-secondary'" @click="openSlots(e)">
              <i class="bi bi-calendar3 me-1"></i>
              {{ e.status === 'Booking Open' ? 'View Slots & Book' : 'View Details' }}
            </button>
          </div>
        </div>
      </div>
      <div v-if="!rows.length" class="col-12 empty-state">
        <div class="empty-icon"><i class="bi bi-journal-x"></i></div>
        <div>No examinations found matching your search.</div>
      </div>
    </div>

    <!-- Slots & Rubric Inspection Modal -->
    <Modal v-if="view" :title="view.exam.name" icon="bi-journal-bookmark" wide @close="view = null">
      <!-- Rubric section -->
      <div class="mb-4">
        <h6 class="fw-bold d-flex align-items-center gap-2 mb-2">
          <i class="bi bi-card-checklist text-primary"></i> Evaluation Rubrics &amp; Criteria
        </h6>
        <div class="table-responsive border rounded">
          <table class="table table-sm mb-0">
            <thead class="bg-light">
              <tr>
                <th>Criterion</th>
                <th>Max Marks</th>
                <th>Weightage</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in view.rubrics" :key="r.id">
                <td class="fw-semibold">{{ r.criterion }}</td>
                <td>{{ r.max_marks }} marks</td>
                <td>{{ r.weightage }}x</td>
                <td class="small text-muted">{{ r.description || '-' }}</td>
              </tr>
              <EmptyRow v-if="!view.rubrics.length" :colspan="4" compact text="Rubrics have not been published for this examination yet." />
            </tbody>
          </table>
        </div>
      </div>

      <!-- Slots section -->
      <div>
        <h6 class="fw-bold d-flex align-items-center gap-2 mb-2">
          <i class="bi bi-calendar-event text-primary"></i> Available Appointment Slots
        </h6>
        <div class="table-responsive border rounded">
          <table class="table table-hover table-sm mb-0 align-middle">
            <thead class="bg-light">
              <tr>
                <th>Date</th>
                <th>Time</th>
                <th>Examiner</th>
                <th>Seats Available</th>
                <th class="text-end">Reservation</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in view.slots" :key="s.id">
                <td class="fw-semibold">{{ fmtDate(s.date) }}</td>
                <td><span class="badge-soft badge-soft-dark">{{ s.start_time }} &ndash; {{ s.end_time }}</span></td>
                <td>{{ s.examiner_name }}</td>
                <td>
                  <span class="badge-soft" :class="s.available_seats > 0 ? 'badge-soft-success' : 'badge-soft-danger'">
                    {{ s.available_seats }} / {{ s.capacity }} seats left
                  </span>
                </td>
                <td class="text-end">
                  <button class="btn btn-sm btn-primary" :disabled="view.exam.status !== 'Booking Open' || s.available_seats <= 0 || s.status !== 'Available'" @click="book(s)">
                    <i class="bi bi-bookmark-check"></i>
                    {{ s.available_seats <= 0 ? 'Full' : 'Book This Slot' }}
                  </button>
                </td>
              </tr>
              <EmptyRow v-if="!view.slots.length" :colspan="5" compact text="No availability slots opened by examiners yet." />
            </tbody>
          </table>
        </div>
      </div>
    </Modal>
  </div>`,
};

const StudentBookings = {
  setup() {
    const rows = ref([]);
    const loading = ref(false);

    const load = async () => {
      loading.value = true;
      try { rows.value = await api('GET', '/bookings'); } finally { loading.value = false; }
    };
    onMounted(load);

    const cancel = async (b) => {
      if (await showConfirm('Are you sure you want to cancel this booking for ' + b.exam_name + '? You can re-book another slot if bookings remain open.', { confirmText: 'Cancel booking', cancelText: 'Keep booking' })) {
        if (await act(() => api('POST', `/bookings/${b.id}/cancel`), 'Booking cancelled successfully')) load();
      }
    };

    return { rows, cancel, badgeClass, fmtDate, loading };
  },
  template: `
  <div>
    <PageHeader />

    <LoadingBlock v-if="loading" />

    <div v-else class="card">
      <div class="table-responsive">
        <table class="table table-hover mb-0 align-middle">
          <thead>
            <tr>
              <th>Examination</th>
              <th>Course</th>
              <th>Date</th>
              <th>Time</th>
              <th>Examiner</th>
              <th>Meeting</th>
              <th>Status</th>
              <th class="text-end">Action</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in rows" :key="b.id">
              <td class="fw-bold text-dark">{{ b.exam_name }}</td>
              <td><span class="badge-soft badge-soft-dark">{{ b.course }}</span></td>
              <td>{{ fmtDate(b.date) }}</td>
              <td><span class="badge-soft badge-soft-dark">{{ b.start_time }} &ndash; {{ b.end_time }}</span></td>
              <td>{{ b.examiner_name }}</td>
              <td>
                <a v-if="b.meeting_link" :href="b.meeting_link" target="_blank" rel="noopener noreferrer" class="small fw-semibold d-inline-flex align-items-center gap-1 text-primary">
                  <i class="bi bi-camera-video"></i> Join Meeting
                </a>
                <span v-else class="text-muted small">&ndash;</span>
              </td>
              <td><StatusBadge :status="b.status" /></td>
              <td class="text-end">
                <button v-if="b.status === 'Booked'" class="btn btn-sm btn-outline-danger" @click="cancel(b)">
                  <i class="bi bi-x-circle"></i> Cancel
                </button>
              </td>
            </tr>
            <EmptyRow v-if="!rows.length" :colspan="8" icon="bi-calendar-x" text="You have no active or previous examination bookings." />
          </tbody>
        </table>
      </div>
    </div>

  </div>`,
};

const StudentResults = {
  components: { Modal },
  setup() {
    const rows = ref([]);
    const sel = ref(null);
    const loading = ref(false);

    onMounted(async () => {
      loading.value = true;
      try { rows.value = await api('GET', '/results'); } finally { loading.value = false; }
    });

    return {
      rows, sel, user: computed(() => state.user || {}),
      print: () => window.print(), fmtDate, loading,
    };
  },
  template: `
  <div>
    <PageHeader />

    <LoadingBlock v-if="loading" />

    <div v-else class="card">
      <div class="table-responsive">
        <table class="table table-hover mb-0 align-middle">
          <thead>
            <tr>
              <th>Examination</th>
              <th>Course</th>
              <th>Marks Secured</th>
              <th>Examiner Feedback</th>
              <th class="text-end">Scorecard</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in rows" :key="b.id">
              <td class="fw-bold text-dark">{{ b.exam_name }}</td>
              <td><span class="badge-soft badge-soft-dark">{{ b.course }}</span></td>
              <td><span class="fs-6 fw-bold text-success">{{ b.evaluation.total_marks }} / {{ b.max_marks }}</span></td>
              <td class="small text-muted" style="max-width:320px;">{{ b.evaluation.remarks || 'No remarks provided.' }}</td>
              <td class="text-end">
                <button class="btn btn-sm btn-primary" @click="sel = b">
                  <i class="bi bi-file-earmark-text"></i> View Scorecard
                </button>
              </td>
            </tr>
            <EmptyRow v-if="!rows.length" :colspan="5" icon="bi-award" text="No examination results have been published yet. Check back soon." />
          </tbody>
        </table>
      </div>
    </div>

    <!-- Official Printable Scorecard Modal -->
    <Modal v-if="sel" title="Official Academic Scorecard" icon="bi-award" wide @close="sel = null">
      <div class="p-4 border rounded bg-white" style="border-top: 4px solid var(--accent) !important;">
        <!-- Header -->
        <div class="text-center pb-3 mb-3 border-bottom">
          <div class="d-inline-flex p-2 rounded-circle bg-primary-subtle text-primary mb-2">
            <i class="bi bi-mortarboard-fill fs-3"></i>
          </div>
          <h5 class="fw-bold mb-0">EXAMINATION MANAGEMENT PORTAL</h5>
          <div class="text-muted small text-uppercase" style="letter-spacing: 0.08em;">Official Assessment Transcript &middot; MAD 2</div>
        </div>

        <!-- Details Grid -->
        <div class="row g-2 mb-4 small">
          <div class="col-6">
            <div class="text-muted">Student Name:</div>
            <div class="fw-bold">{{ user.name }} ({{ user.email }})</div>
          </div>
          <div class="col-6">
            <div class="text-muted">Examination:</div>
            <div class="fw-bold">{{ sel.exam_name }} ({{ sel.course }})</div>
          </div>
          <div class="col-6">
            <div class="text-muted">Examiner:</div>
            <div class="fw-bold">{{ sel.examiner_name }}</div>
          </div>
          <div class="col-6">
            <div class="text-muted">Evaluation Date:</div>
            <div class="fw-bold">{{ fmtDate(sel.date) }}</div>
          </div>
        </div>

        <!-- Criteria Marks Breakdown -->
        <div class="table-responsive mb-3">
          <table class="table table-bordered table-sm align-middle">
            <thead class="bg-light">
              <tr>
                <th>Rubric Criterion</th>
                <th class="text-center" style="width: 140px;">Marks Scored</th>
                <th class="text-center" style="width: 140px;">Max Marks</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in sel.evaluation.scores" :key="s.rubric_id">
                <td>{{ s.criterion }}</td>
                <td class="text-center fw-semibold text-primary">{{ s.marks }}</td>
                <td class="text-center text-muted">{{ s.max_marks }}</td>
              </tr>
            </tbody>
            <tfoot class="table-light fw-bold">
              <tr>
                <td>Final Weighted Total</td>
                <td class="text-center text-success fs-6">{{ sel.evaluation.total_marks }}</td>
                <td class="text-center fs-6">{{ sel.max_marks }}</td>
              </tr>
            </tfoot>
          </table>
        </div>

        <!-- Feedback & Signatures -->
        <div class="panel-inset mb-4">
          <div class="small fw-semibold text-muted text-uppercase mb-1">Examiner Remarks</div>
          <div class="small text-secondary">{{ sel.evaluation.remarks || 'No remarks provided.' }}</div>
        </div>

        <div class="row pt-4 text-center small text-muted">
          <div class="col-6">
            <div class="pt-2 border-top mx-auto" style="max-width: 180px;">Evaluated By (Examiner)</div>
          </div>
          <div class="col-6">
            <div class="pt-2 border-top mx-auto" style="max-width: 180px;">Controller of Examinations</div>
          </div>
        </div>
      </div>

      <div class="text-end mt-3 no-print">
        <button class="btn btn-primary" @click="print">
          <i class="bi bi-printer"></i> Print / Save as PDF
        </button>
      </div>
    </Modal>
  </div>`,
};

const Profile = {
  setup() {
    const f = reactive({ name: '', phone: '', department: '', password: '' });
    const saving = ref(false);

    onMounted(async () => {
      Object.assign(f, await api('GET', '/auth/me'), { password: '' });
    });

    const save = async () => {
      saving.value = true;
      try {
        const r = await act(() => api('PUT', '/auth/me', f), 'Profile changes saved');
        if (r && r.name) {
          state.user = r;
          localStorage.setItem('user', JSON.stringify(r));
          f.password = '';
        }
      } finally {
        saving.value = false;
      }
    };

    return { f, saving, save };
  },
  template: `
  <div>
    <PageHeader />

    <div class="card" style="max-width: 580px;">
      <div class="card-body p-4">
        <form @submit.prevent="save">
          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">Full Name <span class="text-danger">*</span></label>
            <input class="form-control" v-model="f.name" required>
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">Email Address (Read-only)</label>
            <input class="form-control bg-light" :value="f.email" disabled>
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">Contact Phone Number</label>
            <input class="form-control" v-model="f.phone" pattern="[0-9+\\- ]{7,15}" placeholder="+91-...">
          </div>

          <div class="mb-3">
            <label class="form-label small fw-semibold text-muted">Department / Division</label>
            <input class="form-control" v-model="f.department" placeholder="e.g. Computer Science">
          </div>

          <div class="mb-4">
            <label class="form-label small fw-semibold text-muted">Change Password (optional)</label>
            <input type="password" class="form-control" v-model="f.password" minlength="6" placeholder="Leave blank to preserve current password">
          </div>

          <button class="btn btn-primary" :disabled="saving">
            <span v-if="saving" class="spinner-border spinner-border-sm me-1"></span>
            <i v-else class="bi bi-check2"></i>
            <span>Update Profile</span>
          </button>
        </form>
      </div>
    </div>
  </div>`,
};

/* ---------------- Root Shell & Navigation ---------------- */
const navLinks = Object.fromEntries(Object.entries({
  admin: [['/admin', 'bi-speedometer2'], ['/admin/courses', 'bi-book-half'], ['/admin/exams', 'bi-journal-check'],
    ['/admin/examiners', 'bi-person-badge'], ['/admin/students', 'bi-mortarboard'], ['/admin/slots', 'bi-calendar3-range'],
    ['/admin/search', 'bi-search'], ['/profile', 'bi-gear']],
  examiner: [['/examiner', 'bi-speedometer2'], ['/examiner/slots', 'bi-calendar-check'],
    ['/examiner/evaluate', 'bi-check2-square'], ['/profile', 'bi-gear']],
  student: [['/student', 'bi-speedometer2'], ['/student/exams', 'bi-journal-text'], ['/student/bookings', 'bi-calendar3-event'],
    ['/student/results', 'bi-trophy'], ['/profile', 'bi-gear']],
}).map(([role, items]) => [role, items.map(([path, icon]) => [path, pageMeta[path].title, icon])]));

const App = {
  components: { ConfirmModal },
  setup() {
    const notifOpen = ref(false);
    const navOpen = ref(false);
    let timer;

    const loadNotifications = async () => {
      if (state.user) {
        try { state.notifs = await api('GET', '/notifications'); } catch (e) { /* ignore */ }
      }
    };

    onMounted(() => {
      loadNotifications();
      timer = setInterval(() => {
        if (!document.hidden && state.user) loadNotifications();
      }, 45000);
    });
    onBeforeUnmount(() => clearInterval(timer));
    watch(() => state.user, loadNotifications);

    const unreadCount = computed(() => state.notifs.filter((n) => !n.read).length);

    const toggleNotifs = async () => {
      notifOpen.value = !notifOpen.value;
      if (!notifOpen.value && unreadCount.value) {
        await api('POST', '/notifications/read');
        loadNotifications();
      }
    };

    const markAllRead = async () => {
      await api('POST', '/notifications/read');
      loadNotifications();
    };

    const route = VueRouter.useRoute();
    const currentTitle = computed(() => {
      if (!state.user) return 'Examination Management Portal';
      const list = navLinks[state.user.role] || [];
      const item = list.find((x) => x[0] === route.path);
      return item ? item[1] : 'Examination Management Portal';
    });

    const userInitial = computed(() => (state.user ? state.user.name.charAt(0).toUpperCase() : 'U'));
    const answerConfirm = (ok) => {
      const c = state.confirm;
      state.confirm = null;
      if (c) c.resolve(ok);
    };

    return {
      state, navLinks, notifOpen, navOpen, unreadCount,
      toggleNotifs, markAllRead, logout, currentTitle, userInitial, answerConfirm,
    };
  },
  template: `
  <div v-if="state.user" :class="'role-' + state.user.role">
    <!-- Sidebar -->
    <aside class="sidebar no-print" :class="{ open: navOpen }">
      <div class="brand">
        <div class="logo-icon">E</div>
        <div class="brand-meta">
          <div class="brand-title">EMP Portal</div>
          <div class="brand-sub">{{ state.user.role }} workspace</div>
        </div>
      </div>

      <div class="nav-section">
        <div class="nav-label">Navigation Menu</div>
        <router-link v-for="l in navLinks[state.user.role]" :key="l[0]" :to="l[0]" class="side-link" @click="navOpen = false">
          <i :class="'bi ' + l[2]"></i>
          <span>{{ l[1] }}</span>
        </router-link>
      </div>

      <div class="sidebar-footer">
        <div class="role-badge-box">
          <div class="avatar" style="width:28px;height:28px;font-size:0.8rem;">{{ userInitial }}</div>
          <div class="flex-grow-1 overflow-hidden">
            <div class="fw-semibold text-truncate small">{{ state.user.name }}</div>
            <div class="text-muted text-uppercase" style="font-size:0.65rem;letter-spacing:0.05em;">{{ state.user.role }}</div>
          </div>
        </div>
      </div>
    </aside>

    <!-- Mobile Overlay -->
    <div class="scrim d-lg-none no-print" v-if="navOpen" @click="navOpen = false"></div>

    <!-- Main Viewport -->
    <div class="main">
      <header class="topbar no-print">
        <button class="icon-btn d-lg-none" @click="navOpen = !navOpen" aria-label="Toggle Navigation">
          <i class="bi bi-list fs-5"></i>
        </button>

        <div class="page-heading crumbs">
          <span class="text-capitalize">{{ state.user.role }}</span>
          <i class="bi bi-chevron-right"></i>
          <strong>{{ currentTitle }}</strong>
        </div>

        <!-- Notification Bell -->
        <div class="position-relative">
          <button class="icon-btn" @click="toggleNotifs" aria-label="Notifications">
            <i class="bi bi-bell fs-5"></i>
            <span v-if="unreadCount" class="badge-dot"></span>
          </button>

          <!-- Notifications Dropdown Flyout -->
          <div v-if="notifOpen" class="notif-panel">
            <div class="notif-header">
              <span>Notifications <span v-if="unreadCount" class="badge-soft badge-soft-danger ms-1">{{ unreadCount }} new</span></span>
              <button class="btn btn-link btn-sm text-decoration-none p-0" style="font-size:0.75rem" @click="markAllRead">
                Mark all read
              </button>
            </div>
            <div class="notif-list">
              <div class="notif-item" v-for="n in state.notifs" :key="n.id" :class="{ unread: !n.read }">
                <div class="fw-medium text-dark">{{ n.message }}</div>
                <div class="text-muted small mt-1">
                  <i class="bi bi-clock me-1"></i>{{ (n.created_at || '').slice(0, 16).replace('T', ' ') }}
                </div>
              </div>
              <div v-if="!state.notifs.length" class="text-center py-4 text-muted small">
                <i class="bi bi-bell-slash fs-3 d-block text-secondary mb-1"></i>
                No notifications right now.
              </div>
            </div>
          </div>
        </div>

        <!-- User Info & Avatar -->
        <div class="d-none d-md-flex align-items-center gap-2">
          <div class="text-end lh-sm">
            <div class="small fw-bold text-dark">{{ state.user.name }}</div>
            <div class="text-muted" style="font-size:0.72rem">{{ state.user.email }}</div>
          </div>
          <div class="avatar" :title="state.user.name">{{ userInitial }}</div>
        </div>

        <!-- Logout Button -->
        <button class="btn btn-sm btn-outline-secondary" @click="logout" title="Sign Out" aria-label="Sign Out">
          <i class="bi bi-box-arrow-right"></i>
          <span class="d-none d-sm-inline">Sign Out</span>
        </button>
      </header>

      <main class="content">
        <router-view />
      </main>
    </div>
  </div>

  <div v-else>
    <router-view />
  </div>

  <ConfirmModal v-if="state.confirm" :title="state.confirm.title" :message="state.confirm.message"
                :confirm-text="state.confirm.confirmText" :cancel-text="state.confirm.cancelText"
                :danger="state.confirm.danger !== false" @confirm="answerConfirm(true)" @cancel="answerConfirm(false)" />

  <!-- Toast Container -->
  <div class="toast-box no-print" v-if="state.toast">
    <div class="alert toast-pill text-white shadow-lg" :class="state.toast.type === 'danger' ? 'bg-danger' : 'bg-dark'">
      <i :class="state.toast.type === 'danger' ? 'bi bi-exclamation-triangle-fill fs-5' : 'bi bi-check-circle-fill text-success fs-5'"></i>
      <div class="small fw-semibold">{{ state.toast.msg }}</div>
    </div>
  </div>`,
};

/* ---------------- Client-Side Routes ---------------- */
const routes = [
  { path: '/', redirect: () => (state.user ? '/' + state.user.role : '/login') },
  { path: '/login', component: Login, meta: { public: true } },
  { path: '/register', component: Register, meta: { public: true } },
  { path: '/admin', component: AdminDash, meta: { role: 'admin' } },
  { path: '/admin/courses', component: AdminCourses, meta: { role: 'admin' } },
  { path: '/admin/exams', component: AdminExams, meta: { role: 'admin' } },
  { path: '/admin/examiners', component: AdminExaminers, meta: { role: 'admin' } },
  { path: '/admin/students', component: AdminStudents, meta: { role: 'admin' } },
  { path: '/admin/slots', component: AdminSlots, meta: { role: 'admin' } },
  { path: '/admin/search', component: AdminSearch, meta: { role: 'admin' } },
  { path: '/examiner', component: ExaminerDash, meta: { role: 'examiner' } },
  { path: '/examiner/slots', component: ExaminerSlots, meta: { role: 'examiner' } },
  { path: '/examiner/evaluate', component: ExaminerEvaluate, meta: { role: 'examiner' } },
  { path: '/student', component: StudentDash, meta: { role: 'student' } },
  { path: '/student/exams', component: StudentExams, meta: { role: 'student' } },
  { path: '/student/bookings', component: StudentBookings, meta: { role: 'student' } },
  { path: '/student/results', component: StudentResults, meta: { role: 'student' } },
  { path: '/profile', component: Profile, meta: { role: ['admin', 'examiner', 'student'] } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
];

const router = createRouter({ history: createWebHistory(), routes });

router.beforeEach((to) => {
  if (to.meta.public) return state.user ? '/' + state.user.role : true;
  if (!state.user || !token()) return '/login';
  const need = to.meta.role;
  if (need && ![].concat(need).includes(state.user.role)) return '/' + state.user.role;
  return true;
});

const vueApp = createApp(App).use(router);
Object.entries({ PageHeader, HeroBanner, LoadingBlock, EmptyRow, FilterBar, StatCard, StatusBadge, SeatMeter })
  .forEach(([name, component]) => vueApp.component(name, component));
vueApp.mount('#app');