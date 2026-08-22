import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class CalendarScreen extends StatefulWidget {
  const CalendarScreen({super.key});
  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
  List _events = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      _events = (await Api.instance.get('/api/calendar/upcoming') as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Kalender')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _events.isEmpty
                ? Center(child: Text('Keine anstehenden Termine.', style: TextStyle(color: c.dim)))
                : ListView.builder(
                    padding: const EdgeInsets.all(16),
                    itemCount: _events.length,
                    itemBuilder: (context, i) {
                      final e = _events[i];
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: GlassCard(
                          child: Row(children: [
                            const Icon(Icons.event, color: accent),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(e['summary']?.toString() ?? 'Termin', style: TextStyle(color: c.text, fontWeight: FontWeight.w600)),
                                  if ((e['start'] ?? '').toString().isNotEmpty)
                                    Padding(
                                      padding: const EdgeInsets.only(top: 2),
                                      child: Text(e['start'].toString(), style: TextStyle(color: c.dim, fontSize: 11)),
                                    ),
                                ],
                              ),
                            ),
                          ]),
                        ),
                      );
                    },
                  ),
      ),
    );
  }
}
