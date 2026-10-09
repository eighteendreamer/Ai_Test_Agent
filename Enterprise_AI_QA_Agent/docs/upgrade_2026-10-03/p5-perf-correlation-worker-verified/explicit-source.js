import http from "k6/http";
import { check, sleep } from "k6";

const __expectedStatus = [200];
const __responseCallback = http.expectedStatuses(...__expectedStatus);
const __smoke = __ENV.QA_PERF_SMOKE === "true";
const __smokeVus = Number(__ENV.QA_PERF_SMOKE_VUS || 1);
const __smokeIterations = Number(__ENV.QA_PERF_SMOKE_ITERATIONS || 3);

const __loadOptions = {
  vus: 1,
  duration: "1s",
};

const __summaryTrendStats = ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"];
export const options = __smoke
  ? { vus: __smokeVus, iterations: __smokeIterations, summaryTrendStats: __summaryTrendStats }
  : { ...__loadOptions, summaryTrendStats: __summaryTrendStats };

export default function () {
  const __correlations = Object.create(null);
  const __headers0 = {};
  if (true) {
    const res0 = http.get("http://host.docker.internal:61529/plain", {
      headers: __headers0,
      responseCallback: __responseCallback,
    });

    check(res0, {
      ["qa_status_0_" + res0.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

  const __headers1 = {};
  if (true) {
    const res1 = http.get("http://host.docker.internal:61529/login", {
      headers: __headers1,
      responseCallback: __responseCallback,
    });

    check(res1, {
      ["qa_status_1_" + res1.status]: (r) => __expectedStatus.includes(r.status),
    });
    let __value0;
    let __parsed0 = false;
    try {
      __value0 = res1.json("session.credential");
      __parsed0 = true;
    } catch (_) {
      check(false, { "qa_json_parse_0": (v) => v === true });
    }
    const __extracted0 = __parsed0 && __expectedStatus.includes(res1.status) && __value0 !== undefined && __value0 !== null && __value0 !== "";
    check(__extracted0, { "qa_extract_0": (v) => v === true });
    if (__extracted0) __correlations[0] = __value0;
  }

  const __headers2 = {};
  const __bound0 = __correlations[0];
  const __canBind0 = ["string", "number", "boolean"].includes(typeof __bound0) && __bound0 !== "";
  check(__canBind0, { "qa_dependency_0": (v) => v === true });
  if (__canBind0) __headers2["Authorization"] = "Bearer " + String(__bound0);
  if (__canBind0) {
    const res2 = http.get("http://host.docker.internal:61529/profile", {
      headers: __headers2,
      responseCallback: __responseCallback,
    });

    check(res2, {
      ["qa_status_2_" + res2.status]: (r) => __expectedStatus.includes(r.status),
    });
  }

}