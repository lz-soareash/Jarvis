package app.vega.client

import app.vega.client.model.TurnEvent
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TurnEventTest {

    @Test
    fun parseStart() {
        val ev = TurnEvent.parse(JSONObject("""{"type":"start","request_id":"r1"}"""))
        assertEquals(TurnEvent.Start(requestId = "r1"), ev)
    }

    @Test
    fun parseChunk() {
        val ev = TurnEvent.parse(JSONObject("""{"type":"chunk","text":"olá"}"""))
        assertEquals(TurnEvent.Chunk(text = "olá"), ev)
    }

    @Test
    fun parseToolStart() {
        val ev = TurnEvent.parse(JSONObject("""{"type":"tool_start","round":1,"names":["memoria.ler","web"]}"""))
        assertTrue(ev is TurnEvent.ToolStart)
        assertEquals(listOf("memoria.ler", "web"), (ev as TurnEvent.ToolStart).names)
        assertEquals(1, ev.round)
    }

    @Test
    fun parseToolDoneOk() {
        val ev = TurnEvent.parse(JSONObject("""{"type":"tool_done","name":"memoria.ler","ok":true,"detail":"42 registros"}"""))
        assertTrue(ev is TurnEvent.ToolDone)
        assertEquals("memoria.ler", (ev as TurnEvent.ToolDone).name)
        assertEquals(true, ev.ok)
        assertEquals("42 registros", ev.output)
    }

    @Test
    fun parseApprovalRequest() {
        val ev = TurnEvent.parse(
            JSONObject("""{"type":"approval_request","approval":{"id":"ap1","session_id":"s9","tool_name":"exec","risk":"high","permission_level":3}}""")
        )
        assertTrue(ev is TurnEvent.ApprovalRequest)
        val a = (ev as TurnEvent.ApprovalRequest).approval
        assertEquals("ap1", a.id)
        assertEquals("s9", a.sessionId)
        assertEquals("exec", a.toolName)
        assertEquals("high", a.risk)
        assertEquals(3, a.permissionLevel)
    }

    @Test
    fun parseDoneCarriesSessionIdWhenProvided() {
        val ev = TurnEvent.parse(
            JSONObject("""{"type":"done","message":{"content":"resposta final"},"session_id":"ses-1"}""")
        )
        assertTrue(ev is TurnEvent.Done)
        assertEquals("resposta final", (ev as TurnEvent.Done).content)
        assertEquals("ses-1", ev.sessionId)
    }

    @Test
    fun parseDoneWithoutSessionIdFallsBackToDefault() {
        val ev = TurnEvent.parse(
            JSONObject("""{"type":"done","message":{"content":"ok"}}"""),
            defaultSessionId = "fallback",
        )
        assertEquals("fallback", (ev as TurnEvent.Done).sessionId)
    }

    @Test
    fun parseError() {
        val ev = TurnEvent.parse(JSONObject("""{"type":"error","detail":"rate limit"}"""))
        assertTrue(ev is TurnEvent.ErrorEvent)
        assertEquals("rate limit", (ev as TurnEvent.ErrorEvent).detail)
    }

    @Test
    fun parseUnknownTypeReturnsNull() {
        assertNull(TurnEvent.parse(JSONObject("""{"type":"approval_pending","count":1}""")))
        assertNull(TurnEvent.parse(JSONObject("""{"random":true}""")))
    }

    @Test
    fun agentEventIsPassedThrough() {
        val ev = TurnEvent.parse(JSONObject("""{"type":"agent_event","payload":{"k":"v"}}"""))
        assertNotNull(ev)
        assertTrue(ev is TurnEvent.AgentEvent)
        assertEquals("v", (ev as TurnEvent.AgentEvent).payload.optString("k"))
    }

    @Test
    fun approvalFromArrayParsesList() {
        val arr = org.json.JSONArray(
            """[
                {"id":"a1","session_id":"s","tool_name":"t1","risk":"medium","permission_level":2},
                {"id":"a2","session_id":"s","tool_name":"t2","risk":"low","permission_level":1}
            ]"""
        )
        val list = TurnEvent.approvalFromJsonArray(arr)
        assertEquals(2, list.size)
        assertEquals("t1", list[0].toolName)
    }

    @Test
    fun approvalFromNullReturnsEmpty() {
        assertTrue(TurnEvent.approvalFromJsonArray(null).isEmpty())
    }

    @Test
    fun parseToolDoneParsesStructuredJsonOutput() {
        val ev = TurnEvent.parse(
            JSONObject(
                """{"type":"tool_done","name":"mobile_battery_status","ok":true,"output":"{\"type\":\"mobile_command_result\",\"capability\":\"BATTERY_STATUS\",\"status\":\"success\",\"result\":{\"level_percent\":80}}"}"""
            )
        )
        assertTrue(ev is TurnEvent.ToolDone)
        val td = ev as TurnEvent.ToolDone
        assertNotNull(td.structured)
        assertEquals("BATTERY_STATUS", td.structured!!.optString("capability"))
        assertEquals(80, td.structured!!.optJSONObject("result").optInt("level_percent"))
    }

    @Test
    fun parseToolDonePlainDetailHasNoStructured() {
        val ev = TurnEvent.parse(JSONObject("""{"type":"tool_done","name":"memoria.ler","ok":true,"detail":"42 registros"}"""))
        assertTrue(ev is TurnEvent.ToolDone)
        val td = ev as TurnEvent.ToolDone
        assertEquals("42 registros", td.output)
        assertNull(td.structured)
    }
}