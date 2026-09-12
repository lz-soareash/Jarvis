package app.vega.client

import app.vega.client.ui.Block
import app.vega.client.ui.Inline
import app.vega.client.ui.MarkdownParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownParserTest {

    @Test
    fun parsesHeading() {
        val blocks = MarkdownParser.parse("# Título")
        assertEquals(1, blocks.size)
        val h = blocks[0] as Block.Heading
        assertEquals(1, h.level)
        assertEquals(listOf(Inline.Plain("Título")), h.inlines)
    }

    @Test
    fun parsesParagraph() {
        val blocks = MarkdownParser.parse("texto simples")
        assertEquals(1, blocks.size)
        val p = blocks[0] as Block.Paragraph
        assertTrue(p.inlines.contains(Inline.Plain("texto simples")))
    }

    @Test
    fun parsesCodeBlockWithLanguage() {
        val blocks = MarkdownParser.parse("```kotlin\nval x = 1\n```")
        assertEquals(1, blocks.size)
        val c = blocks[0] as Block.CodeBlock
        assertEquals("kotlin", c.language)
        assertEquals("val x = 1", c.text)
    }

    @Test
    fun parsesBulletList() {
        val blocks = MarkdownParser.parse("- a\n- b")
        assertEquals(1, blocks.size)
        val l = blocks[0] as Block.BulletList
        assertEquals(2, l.items.size)
        assertTrue(l.items[0].contains(Inline.Plain("a")))
    }

    @Test
    fun parsesQuote() {
        val blocks = MarkdownParser.parse("> citação")
        assertEquals(1, blocks.size)
        assertTrue(blocks[0] is Block.Quote)
    }

    @Test
    fun parsesDivider() {
        val blocks = MarkdownParser.parse("---")
        assertEquals(1, blocks.size)
        assertTrue(blocks[0] is Block.Divider)
    }

    @Test
    fun inlineCodeSingleBacktick() {
        val inlines = MarkdownParser.parseInline("use `x` aqui")
        assertTrue(inlines[0] is Inline.Plain)
        assertTrue(inlines[1] is Inline.Code)
        assertEquals("x", (inlines[1] as Inline.Code).text)
    }

    @Test
    fun inlineStrong() {
        val inlines = MarkdownParser.parseInline("**forte**")
        val b = inlines.first() as Inline.Bold
        assertTrue(b.children.contains(Inline.Plain("forte")))
    }

    @Test
    fun inlineItalic() {
        val inlines = MarkdownParser.parseInline("*itálico*")
        assertTrue(inlines[0] is Inline.Italic)
    }

    @Test
    fun inlineStrike() {
        val inlines = MarkdownParser.parseInline("~~cortado~~")
        assertTrue(inlines[0] is Inline.Strike)
    }

    @Test
    fun autoLinkHttp() {
        val inlines = MarkdownParser.parseInline("veja https://vega.local/docs")
        assertTrue(inlines.any { it is Inline.Link && it.url.startsWith("https://") })
    }

    @Test
    fun markdownLinkOnlyHttpSchemes() {
        val inlines = MarkdownParser.parseInline("[x](javascript:alert(1))")
        assertTrue(inlines.none { it is Inline.Link })
    }

    @Test
    fun plainTextIsNotEscapedHtmlDanger() {
        val inlines = MarkdownParser.parseInline("<script>alert(1)</script>")
        assertTrue(inlines.all { it is Inline.Plain })
    }
}