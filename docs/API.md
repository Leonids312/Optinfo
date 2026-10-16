# Optinfo v1 API contract

All `/api` endpoints except GET `/api/health` use `Authorization: Bearer <device token>`. Base URL is configurable, with no credentials embedded in binaries. JSON UTF-8. Errors use `{detail: string}`. Asia/Shanghai dates. Single private user with multiple devices.

- GET `/api/health`: `{status, version, generator_ready}`.
- GET `/api/bootstrap`: `{timezone, sections:[{id,label,description}], topics:[{id,label,section,kind,keywords:[]}], active_topics:[], pending_topics:[], effective_date, latest_date, journals:[{id,name}]}`.
- GET `/api/briefing?date=YYYY-MM-DD&q=&section=&journal=&company=&bookmarked=false`: `{date,generated_at,status,items:[],available_dates:[]}`. Empty date means today; no fabricated entries when no edition exists. Search and filters may query historical items when date is empty and q/journal/company/bookmarked set. Sections: research, industry, world.
- GET `/api/items/{id}`: full Item.
- PUT `/api/preferences` body `{topic_ids:[]}`: bootstrap preference fields. Last saved selection becomes effective at next Shanghai calendar day; today's edition and ranking are immutable.
- PUT `/api/items/{id}/bookmark` body `{bookmarked:bool}`: `{bookmarked}`.
- GET `/api/companies?q=&bookmarked=false`: `{items:[Company]}`.
- GET `/api/companies/{id}`: `{company:Company,items:[Item]}`.
- PUT `/api/companies/{id}/bookmark` body `{bookmarked:bool}`: `{bookmarked}`.
- GET `/api/items/{id}/chat`: Chat.
- POST `/api/items/{id}/chat` body `{message:string}`: Chat. One thread per item, seven days from creation; after expiry physically remove persisted messages and Harness session resources, then create fresh on next question. Missing model configuration returns explicit 503, not a fabricated reply.

Item: `{id,kind,section,title,summary,published_at,briefing_date,source_url,source_name,doi,journal,company_ids:[],tags:[],importance,content:{background,approach,results,assessment},figures:[{url,caption,source_url}],sources:[{title,url}],evidence_level,bookmarked}`. kind is paper/company/major. content values are plain text, not executable HTML. All optional text fields may be empty. Figures require real source URLs; never generate purported experimental results.

Company: `{id,name,name_en,logo_url,website,description,businesses:[],listed,listing,compensation:{masters,phd,currency,basis,as_of,sources:[]},sources:[{title,url}],bookmarked}`. listed can be true/false/null. Salary strings must be evidence-backed with region, role, annual pre-tax base/total and year; unsupported numbers remain null/"暂无可靠公开数据".

Chat: `{session_id,expires_at,messages:[{role,content,created_at}],status}`. Empty state uses null ids/dates. Retain pending user text on API failure.

Generation: persistent database; daily 04:00 Asia/Shanghai with lock, idempotent date edition, recoverable restart and source failure isolation. User preferences are applied using edition date. Separate frequent major-event collection may add events without replacing the edition. Bookmarks survive regeneration and chat deletion.
