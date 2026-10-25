import { flushPromises, type VueWrapper } from "@vue/test-utils";
import { NAlert, NButton, NCheckbox, NFormItem, NInput, NInputNumber, NSelect, NTabs } from "naive-ui";

export function allButtons(wrapper: VueWrapper) {
  return wrapper.findAllComponents(NButton);
}

export function buttonByText(wrapper: VueWrapper, text: string) {
  return allButtons(wrapper).find(button => button.text() === text);
}

export async function clickButton(wrapper: VueWrapper, text: string) {
  const button = buttonByText(wrapper, text);
  if (!button) throw new Error(`未找到按钮：${text}；现有：${allButtons(wrapper).map(item => item.text()).join(" | ")}`);
  await button.trigger("click");
  await flushPromises();
  return button;
}

export function buttonTexts(wrapper: VueWrapper) {
  return allButtons(wrapper).map(button => button.text());
}

export function allInputs(wrapper: VueWrapper) {
  return wrapper.findAllComponents(NInput);
}

export async function setInput(wrapper: VueWrapper, index: number, value: unknown) {
  allInputs(wrapper)[index].vm.$emit("update:value", value);
  await flushPromises();
}

export function inputByLabel(wrapper: VueWrapper, label: string) {
  const item = wrapper.findAllComponents(NFormItem).find(formItem => formItem.text().startsWith(label));
  return item?.findComponent(NInput);
}

export async function setInputByLabel(wrapper: VueWrapper, label: string, value: string) {
  const input = inputByLabel(wrapper, label);
  if (!input) throw new Error(`未找到表单项：${label}`);
  input.vm.$emit("update:value", value);
  await flushPromises();
}

export function allSelects(wrapper: VueWrapper) {
  return wrapper.findAllComponents(NSelect);
}

export async function setSelect(wrapper: VueWrapper, value: unknown, index = 0) {
  allSelects(wrapper)[index].vm.$emit("update:value", value);
  await flushPromises();
}

export function selectByLabel(wrapper: VueWrapper, label: string) {
  const item = wrapper.findAllComponents(NFormItem).find(formItem => formItem.text().startsWith(label));
  return item?.findComponent(NSelect);
}

export async function setSelectByLabel(wrapper: VueWrapper, label: string, value: unknown) {
  const select = selectByLabel(wrapper, label);
  if (!select) throw new Error(`未找到下拉项：${label}`);
  select.vm.$emit("update:value", value);
  await flushPromises();
}

export function allCheckboxes(wrapper: VueWrapper) {
  return wrapper.findAllComponents(NCheckbox);
}

export function checkboxByTestId(wrapper: VueWrapper, testId: string) {
  return allCheckboxes(wrapper).find(checkbox => checkbox.attributes("data-testid") === testId);
}

export function checkboxByText(wrapper: VueWrapper, text: string) {
  return allCheckboxes(wrapper).find(checkbox => checkbox.text().includes(text));
}

export async function setCheckbox(wrapper: VueWrapper, testId: string, checked: boolean) {
  const checkbox = checkboxByTestId(wrapper, testId);
  if (!checkbox) throw new Error(`未找到复选框：${testId}`);
  checkbox.vm.$emit("update:checked", checked);
  await flushPromises();
}

export function allInputNumbers(wrapper: VueWrapper) {
  return wrapper.findAllComponents(NInputNumber);
}

export function inputNumberByTestId(wrapper: VueWrapper, testId: string) {
  return allInputNumbers(wrapper).find(input => input.attributes("data-testid") === testId);
}

export async function setInputNumber(wrapper: VueWrapper, testId: string, value: number) {
  const input = inputNumberByTestId(wrapper, testId);
  if (!input) throw new Error(`未找到数字输入：${testId}`);
  input.vm.$emit("update:value", value);
  await flushPromises();
}

export async function setTab(wrapper: VueWrapper, value: string | number) {
  wrapper.findAllComponents(NTabs)[0].vm.$emit("update:value", value);
  await flushPromises();
}

export function hasAlert(wrapper: VueWrapper, type?: string) {
  return wrapper.findAllComponents(NAlert).some(alert => !type || alert.props("type") === type);
}

export function dialogText() {
  return document.body.textContent || "";
}

export function dialogElement<T extends Element = Element>(selector: string) {
  return document.body.querySelector<T>(selector);
}

export async function clickCollapseHeader(index: number) {
  const headers = document.body.querySelectorAll<HTMLElement>(".n-collapse-item__header-main");
  headers[index]?.click();
  await flushPromises();
}
