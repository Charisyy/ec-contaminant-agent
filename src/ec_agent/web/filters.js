/* Catalog filters: OR within a filter, AND between filters. No model requests. */
class CatalogFilter {
  constructor(element, label, emptyLabel, options = []) {
    this.element = element;
    this.label = label;
    this.emptyLabel = emptyLabel;
    this.options = [];
    this.selected = new Set();
    const summary = document.createElement('summary');
    summary.setAttribute('aria-label', label + '（可多选）');
    this.caption = document.createElement('span');
    summary.append(this.caption);
    const panel = document.createElement('div');
    panel.className = 'catalog-filter-panel';
    const heading = document.createElement('div');
    heading.className = 'catalog-filter-heading';
    const title = document.createElement('strong');
    title.textContent = label + ' · 可多选';
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.textContent = '清空';
    clear.setAttribute('aria-label', '清空' + label);
    clear.onclick = () => this.clear(true);
    heading.append(title, clear);
    this.list = document.createElement('div');
    this.list.className = 'catalog-filter-options';
    this.list.setAttribute('role', 'group');
    this.list.setAttribute('aria-label', label);
    const hint = document.createElement('p');
    hint.className = 'catalog-filter-hint';
    hint.textContent = '未勾选时显示全部';
    panel.append(heading, this.list, hint);
    element.append(summary, panel);
    element.addEventListener('toggle', () => {
      if (element.open) document.querySelectorAll('.catalog-filter[open]').forEach(other => {
        if (other !== element) other.open = false;
      });
    });
    element.addEventListener('keydown', event => {
      if (event.key === 'Escape') { element.open = false; summary.focus(); }
    });
    this.setOptions(options);
  }

  setOptions(options) {
    this.options = options;
    this.selected = new Set([...this.selected].filter(value => options.some(option => option.value === value)));
    this.list.replaceChildren();
    options.forEach(option => {
      const row = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = option.value;
      checkbox.checked = this.selected.has(option.value);
      const text = document.createElement('span');
      text.textContent = option.label;
      row.append(checkbox, text);
      if (option.count !== undefined) {
        const count = document.createElement('small');
        count.textContent = option.count.toLocaleString();
        row.append(count);
      }
      checkbox.onchange = event => {
        event.stopPropagation();
        if (checkbox.checked) this.selected.add(option.value);
        else this.selected.delete(option.value);
        this.refresh();
        this.element.dispatchEvent(new Event('change'));
      };
      this.list.append(row);
    });
    this.refresh();
  }

  refresh() {
    const values = [...this.selected];
    this.caption.textContent = values.length === 0 ? this.emptyLabel : values.length === 1
      ? this.options.find(option => option.value === values[0]).label
      : this.label + ' · 已选 ' + values.length + ' 项';
    this.element.classList.toggle('has-selection', values.length > 0);
    this.element.querySelector('summary').title = this.options.filter(option => this.selected.has(option.value)).map(option => option.label).join('、') || this.emptyLabel;
  }

  clear(notify = false) {
    this.selected.clear();
    this.list.querySelectorAll('input').forEach(input => { input.checked = false; });
    this.refresh();
    if (notify) this.element.dispatchEvent(new Event('change'));
  }

  appendTo(params) {
    this.selected.forEach(value => params.append(this.element.id, value));
  }
}

document.addEventListener('click', event => {
  document.querySelectorAll('.catalog-filter[open]').forEach(element => {
    if (!element.contains(event.target)) element.open = false;
  });
});
