import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class InboxTab extends StatefulWidget {
  const InboxTab({super.key});
  @override
  State<InboxTab> createState() => _InboxTabState();
}

class _InboxTabState extends State<InboxTab> {
  List _mails = [];
  List _upcoming = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final results = await Future.wait([
        Api.instance.get('/api/mails/inbox'),
        Api.instance.get('/api/calendar/upcoming'),
      ]);
      _mails = ((results[0] as List?) ?? []).where((m) => m['read'] != true).take(20).toList();
      _upcoming = (results[1] as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: _load,
      child: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text('Posteingang', style: TextStyle(color: textMain, fontSize: 24, fontWeight: FontWeight.w800)),
                const SizedBox(height: 16),
                if (_upcoming.isNotEmpty) ...[
                  const Text('Termine', style: TextStyle(color: textMain, fontSize: 15, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 8),
                  ..._upcoming.take(3).map((e) => Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: GlassCard(
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                          child: Row(children: [
                            const Icon(Icons.event, color: accent, size: 18),
                            const SizedBox(width: 10),
                            Expanded(child: Text(e['summary']?.toString() ?? '', style: const TextStyle(color: textMain, fontSize: 13), overflow: TextOverflow.ellipsis)),
                          ]),
                        ),
                      )),
                  const SizedBox(height: 16),
                ],
                Text('Ungelesen (${_mails.length})', style: const TextStyle(color: textMain, fontSize: 15, fontWeight: FontWeight.w700)),
                const SizedBox(height: 8),
                if (_mails.isEmpty)
                  const GlassCard(child: Text('Keine ungelesenen Mails.', style: TextStyle(color: textDim)))
                else
                  ..._mails.map((m) => Padding(
                        padding: const EdgeInsets.only(bottom: 8),
                        child: GlassCard(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(m['from_addr']?.toString() ?? '?', style: const TextStyle(color: accent, fontSize: 12, fontWeight: FontWeight.w700)),
                              const SizedBox(height: 4),
                              Text(m['subject']?.toString() ?? 'Kein Betreff', style: const TextStyle(color: textMain, fontSize: 13), maxLines: 2, overflow: TextOverflow.ellipsis),
                            ],
                          ),
                        ),
                      )),
              ],
            ),
    );
  }
}
