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
    const res = http.get("http://host.docker.internal:61973/login", {
      headers: __headers0,
      responseCallback: __responseCallback,
    });

    check(res, {
      ["qa_status_0_" + res.status]: (r) => __expectedStatus.includes(r.status),
    });
    let __value0;
    let __parsed0 = false;
    try {
      __value0 = res.json("session.credential");
      __parsed0 = true;
    } catch (_) {
      check(false, { "qa_json_parse_0": (v) => v === true });
    }
    const __extracted0 = __parsed0 && __expectedStatus.includes(res.status) && __value0 !== undefined && __value0 !== null && __value0 !== "";
    check(__extracted0, { "qa_extract_0": (v) => v === true });
    if (__extracted0) __correlations[0] = __value0;
  }

}