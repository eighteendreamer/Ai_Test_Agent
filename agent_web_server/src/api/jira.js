import api from './index'

export const jiraAPI = {
  pushBugs(bug_ids, project_key, issue_type = 'Bug', priority_name = null) {
    return api.post('/jira/bugs/push', { bug_ids, project_key, issue_type, priority_name })
  },
  syncBugStatus(bug_id = null) {
    const params = {}
    if (bug_id) params.bug_id = bug_id
    return api.post('/jira/bugs/sync', null, { params })
  }
}
