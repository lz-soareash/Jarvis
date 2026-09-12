package app.vega.client.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.vega.client.ui.theme.VegaColors

sealed class Block {
    data class Heading(val level: Int, val inlines: List<Inline>) : Block()
    data class Paragraph(val inlines: List<Inline>) : Block()
    data class CodeBlock(val language: String, val text: String) : Block()
    data class BulletList(val items: List<List<Inline>>) : Block()
    data class Quote(val inlines: List<Inline>) : Block()
    object Divider : Block()
}

sealed class Inline {
    data class Plain(val text: String) : Inline()
    data class Bold(val children: List<Inline>) : Inline()
    data class Italic(val children: List<Inline>) : Inline()
    data class Strike(val children: List<Inline>) : Inline()
    data class Code(val text: String) : Inline()
    data class Link(val url: String, val children: List<Inline>) : Inline()
}

object MarkdownParser {

    fun parse(source: String): List<Block> {
        val out = mutableListOf<Block>()
        val lines = source.replace("\r\n", "\n").split("\n")
        var i = 0
        while (i < lines.size) {
            val line = lines[i]
            val trimmed = line.trim()
            when {
                trimmed.startsWith("```") -> {
                    val language = trimmed.removePrefix("```").trim()
                    val buf = StringBuilder()
                    i++
                    while (i < lines.size && !lines[i].trimStart().startsWith("```")) {
                        buf.append(lines[i])
                        if (i < lines.size - 1) buf.append("\n")
                        i++
                    }
                    if (i < lines.size) i++
                    out.add(Block.CodeBlock(language, buf.toString().trimEnd('\n')))
                }
                trimmed.startsWith("### ") -> {
                    out.add(Block.Heading(3, parseInline(trimmed.removePrefix("### "))))
                    i++
                }
                trimmed.startsWith("## ") -> {
                    out.add(Block.Heading(2, parseInline(trimmed.removePrefix("## "))))
                    i++
                }
                trimmed.startsWith("# ") -> {
                    out.add(Block.Heading(1, parseInline(trimmed.removePrefix("# "))))
                    i++
                }
                trimmed == "---" -> {
                    out.add(Block.Divider)
                    i++
                }
                trimmed.startsWith("> ") -> {
                    val buf = StringBuilder()
                    var j = i
                    while (j < lines.size && lines[j].trimStart().startsWith("> ")) {
                        if (buf.isNotEmpty()) buf.append("\n")
                        buf.append(lines[j].trimStart().removePrefix("> "))
                        j++
                    }
                    out.add(Block.Quote(parseInline(buf.toString())))
                    i = j
                }
                trimmed.startsWith("- ") || trimmed.startsWith("* ") -> {
                    val items = mutableListOf<List<Inline>>()
                    while (i < lines.size) {
                        val t = lines[i].trim()
                        if (t.startsWith("- ")) items.add(parseInline(t.removePrefix("- ")))
                        else if (t.startsWith("* ")) items.add(parseInline(t.removePrefix("* ")))
                        else break
                        i++
                    }
                    out.add(Block.BulletList(items))
                }
                trimmed.isEmpty() -> i++
                else -> {
                    val buf = StringBuilder()
                    var j = i
                    while (j < lines.size && lines[j].isNotBlank()) {
                        val t = lines[j].trim()
                        if (t.startsWith("```") || t.startsWith("#") || t.startsWith("- ") ||
                            t.startsWith("* ") || t.startsWith("> ") || t == "---"
                        ) break
                        if (buf.isNotEmpty()) buf.append("\n")
                        buf.append(t)
                        j++
                    }
                    out.add(Block.Paragraph(parseInline(buf.toString())))
                    i = j
                }
            }
        }
        return out
    }

    fun parseInline(source: String): List<Inline> {
        val out = mutableListOf<Inline>()
        var i = 0
        while (i < source.length) {
            when {
                source.startsWith("~~", i) -> {
                    val j = source.indexOf("~~", i + 2)
                    if (j > i + 2) {
                        out.add(Inline.Strike(parseInline(source.substring(i + 2, j))))
                        i = j + 2
                    } else {
                        out.add(Inline.Plain("~~"))
                        i += 2
                    }
                }
                source.startsWith("**", i) -> {
                    val j = source.indexOf("**", i + 2)
                    if (j > i + 2) {
                        out.add(Inline.Bold(parseInline(source.substring(i + 2, j))))
                        i = j + 2
                    } else {
                        out.add(Inline.Plain("**"))
                        i += 2
                    }
                }
                source[i] == '*' -> {
                    val j = source.indexOf("*", i + 1)
                    if (j > i + 1) {
                        out.add(Inline.Italic(parseInline(source.substring(i + 1, j))))
                        i = j + 1
                    } else {
                        out.add(Inline.Plain("*"))
                        i++
                    }
                }
                source[i] == '_' -> {
                    val j = source.indexOf("_", i + 1)
                    if (j > i + 1) {
                        out.add(Inline.Italic(parseInline(source.substring(i + 1, j))))
                        i = j + 1
                    } else {
                        out.add(Inline.Plain("_"))
                        i++
                    }
                }
                source[i] == '`' -> {
                    var k = i
                    while (k < source.length && source[k] == '`') k++
                    val ticks = k - i
                    val close = source.indexOf("`".repeat(ticks), k)
                    if (close > k) {
                        out.add(Inline.Code(source.substring(k, close)))
                        i = close + ticks
                    } else {
                        out.add(Inline.Plain("`".repeat(ticks)))
                        i = k
                    }
                }
                source[i] == '[' -> {
                    val labelEnd = source.indexOf("](", i)
                    if (labelEnd > i) {
                        val urlEnd = source.indexOf(")", labelEnd + 2)
                        if (urlEnd > labelEnd + 2) {
                            val label = source.substring(i + 1, labelEnd)
                            val url = source.substring(labelEnd + 2, urlEnd)
                            if (url.startsWith("http://") || url.startsWith("https://")) {
                                out.add(Inline.Link(url, parseInline(label)))
                                i = urlEnd + 1
                            } else {
                                out.add(Inline.Plain("["))
                                i++
                            }
                        } else {
                            out.add(Inline.Plain("["))
                            i++
                        }
                    } else {
                        out.add(Inline.Plain("["))
                        i++
                    }
                }
                source.startsWith("http://", i) || source.startsWith("https://", i) -> {
                    var j = i
                    while (j < source.length && !source[j].isWhitespace() && source[j] != ')') j++
                    out.add(Inline.Link(source.substring(i, j), listOf(Inline.Plain(source.substring(i, j)))))
                    i = j
                }
                else -> {
                    val specials = listOf('*', '_', '`', '[', '~')
                    var j = i
                    while (j < source.length && !specials.contains(source[j]) &&
                        !source.startsWith("http://", j) && !source.startsWith("https://", j)
                    ) j++
                    out.add(Inline.Plain(source.substring(i, j)))
                    i = j
                }
            }
        }
        return out
    }
}

private fun AnnotatedString.Builder.appendInline(inlines: List<Inline>) {
    for (inline in inlines) {
        when (inline) {
            is Inline.Plain -> append(inline.text)
            is Inline.Code -> withStyle(
                SpanStyle(
                    color = VegaColors.PrimaryActive,
                    background = VegaColors.Surface2,
                    fontFamily = FontFamily.Monospace,
                ),
            ) { append(inline.text) }
            is Inline.Bold -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { appendInline(inline.children) }
            is Inline.Italic -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { appendInline(inline.children) }
            is Inline.Strike -> withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough, color = VegaColors.TextMuted)) {
                appendInline(inline.children)
            }
            is Inline.Link -> withStyle(SpanStyle(color = VegaColors.Primary, textDecoration = TextDecoration.Underline)) {
                appendInline(inline.children)
            }
        }
    }
}

@Composable
fun MarkdownContent(
    blocks: List<Block>,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier) {
        for (block in blocks) {
            when (block) {
                is Block.Heading -> {
                    val fontSize = when (block.level) {
                        1 -> 18.sp
                        2 -> 16.sp
                        else -> 14.sp
                    }
                    val annotated = buildAnnotatedString {
                        withStyle(SpanStyle(fontWeight = FontWeight.Bold, color = VegaColors.TextPrimary)) {
                            appendInline(block.inlines)
                        }
                    }
                    Text(annotated, fontSize = fontSize, color = VegaColors.TextPrimary, modifier = Modifier.padding(top = 8.dp, bottom = 4.dp))
                }
                is Block.Paragraph -> {
                    val annotated = buildAnnotatedString {
                        withStyle(SpanStyle(color = VegaColors.TextPrimary, fontSize = 14.sp)) {
                            appendInline(block.inlines)
                        }
                    }
                    Text(annotated, fontSize = 14.sp, color = VegaColors.TextPrimary, modifier = Modifier.padding(vertical = 4.dp))
                }
                is Block.Quote -> {
                    val annotated = buildAnnotatedString {
                        withStyle(SpanStyle(color = VegaColors.TextSecondary, fontStyle = FontStyle.Italic)) {
                            appendInline(block.inlines)
                        }
                    }
                    Text(
                        annotated,
                        fontSize = 13.sp,
                        modifier = Modifier
                            .padding(vertical = 4.dp)
                            .fillMaxWidth()
                            .background(VegaColors.Surface, RoundedCornerShape(8.dp))
                            .padding(horizontal = 12.dp, vertical = 8.dp),
                    )
                }
                is Block.BulletList -> {
                    for (item in block.items) {
                        val bullet = buildAnnotatedString {
                            append("•  ")
                            withStyle(SpanStyle(color = VegaColors.TextPrimary)) { appendInline(item) }
                        }
                        Text(bullet, fontSize = 14.sp, color = VegaColors.TextPrimary, modifier = Modifier.padding(vertical = 2.dp))
                    }
                }
                is Block.CodeBlock -> {
                    if (block.language.isNotBlank()) {
                        Text(
                            block.language,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 12.sp,
                            color = VegaColors.TextMuted,
                            modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp),
                        )
                    }
                    Text(
                        block.text,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.sp,
                        color = VegaColors.TextSecondary,
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(VegaColors.Surface2, RoundedCornerShape(8.dp))
                            .padding(12.dp),
                    )
                }
                Block.Divider -> {
                    Spacer(Modifier.height(4.dp))
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .height(1.dp)
                            .background(VegaColors.Border),
                    )
                    Spacer(Modifier.height(4.dp))
                }
            }
        }
    }
}