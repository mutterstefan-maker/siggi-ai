import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class ChatMessage {
  final String text;
  final bool fromUser;
  ChatMessage(this.text, this.fromUser);
}

class ChatTab extends StatefulWidget {
  const ChatTab({super.key});
  @override
  State<ChatTab> createState() => _ChatTabState();
}

class _ChatTabState extends State<ChatTab> {
  final _controller = TextEditingController();
  final _scroll = ScrollController();
  final List<ChatMessage> _messages = [ChatMessage('Hallo! Wie kann ich dir helfen?', false)];
  bool _sending = false;

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() {
      _messages.add(ChatMessage(text, true));
      _controller.clear();
      _sending = true;
    });
    _scrollToBottom();
    try {
      final result = await Api.instance.post('/api/jarvis/chat', {'message': text});
      final reply = result?['reply']?.toString() ?? 'Keine Antwort erhalten.';
      setState(() => _messages.add(ChatMessage(reply, false)));
    } catch (_) {
      setState(() => _messages.add(ChatMessage('Fehler bei der Verbindung.', false)));
    }
    setState(() => _sending = false);
    _scrollToBottom();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(_scroll.position.maxScrollExtent, duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          child: Row(children: [
            Container(
              width: 40, height: 40,
              decoration: const BoxDecoration(gradient: accentGradient, shape: BoxShape.circle),
              child: const Icon(Icons.auto_awesome, color: Colors.white, size: 20),
            ),
            const SizedBox(width: 12),
            const Text('Siggi', style: TextStyle(color: textMain, fontSize: 20, fontWeight: FontWeight.w800)),
          ]),
        ),
        Expanded(
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            itemCount: _messages.length,
            itemBuilder: (context, i) {
              final m = _messages[i];
              return Align(
                alignment: m.fromUser ? Alignment.centerRight : Alignment.centerLeft,
                child: Container(
                  margin: const EdgeInsets.only(bottom: 10),
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.75),
                  decoration: BoxDecoration(
                    gradient: m.fromUser ? accentGradient : null,
                    color: m.fromUser ? null : surface,
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: Text(m.text, style: const TextStyle(color: textMain)),
                ),
              );
            },
          ),
        ),
        if (_sending) const Padding(
          padding: EdgeInsets.only(bottom: 8),
          child: Text('Siggi tippt...', style: TextStyle(color: textDim, fontSize: 12)),
        ),
        Padding(
          padding: const EdgeInsets.all(16),
          child: Row(children: [
            Expanded(
              child: TextField(
                controller: _controller,
                style: const TextStyle(color: textMain),
                onSubmitted: (_) => _send(),
                decoration: const InputDecoration(hintText: 'Nachricht an Siggi...'),
              ),
            ),
            const SizedBox(width: 8),
            Container(
              decoration: const BoxDecoration(gradient: accentGradient, shape: BoxShape.circle),
              child: IconButton(icon: const Icon(Icons.send, color: Colors.white), onPressed: _send),
            ),
          ]),
        ),
      ],
    );
  }
}
